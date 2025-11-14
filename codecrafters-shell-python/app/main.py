#!/usr/bin/env python3
import os
import sys
import shlex
import subprocess
import readline
from contextlib import contextmanager, ExitStack

# ----------------------
# Utilities & Globals
# ----------------------
DEBUG = bool(os.getenv("DEBUG"))

def debug(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}", file=sys.stderr)

shell_builtins = {}

def get_env(key, default=""):
    val = os.getenv(key)
    return val if val is not None else default

# ----------------------
# PATH utilities
# ----------------------
def get_path_dirs():
    """Return PATH directories in order (cross-platform)."""
    raw = get_env("PATH", "")
    return raw.split(os.pathsep) if raw else []

_exec_cache = {}
def find_executable_in_path(name):
    """Return first executable full-path for 'name' searching PATH left-to-right."""
    if name in _exec_cache:
        return _exec_cache[name]

    if os.path.isabs(name):
        if os.path.exists(name) and os.access(name, os.X_OK) and os.path.isfile(name):
            _exec_cache[name] = name
            return name
        return None

    for d in get_path_dirs():
        if not d:
            continue
        candidate = os.path.join(d, name)
        if os.path.exists(candidate) and os.access(candidate, os.X_OK) and os.path.isfile(candidate):
            debug(f"find_executable_in_path: found {candidate}")
            _exec_cache[name] = candidate
            return candidate

    _exec_cache[name] = None
    return None

# ----------------------
# Builtin Implementations
# ----------------------
def builtin_echo(args):
    print(" ".join(args))

def builtin_exit(args):
    if len(args) == 0:
        sys.exit(0)
    if len(args) > 1:
        print(f"exit: Invalid number of arguments. Expected 1, given {len(args)}")
        return
    try:
        code = int(args[0])
    except ValueError:
        print(f"exit: {args[0]}: numeric argument required")
        return
    sys.exit(code)

def builtin_type(args):
    if len(args) != 1:
        print(f"type: Invalid number of arguments. Expected 1, given {len(args)}")
        return
    target = args[0]
    if target in shell_builtins:
        print(f"{target} is a shell builtin")
        return
    path = find_executable_in_path(target)
    if path:
        print(f"{target} is {path}")
    else:
        print(f"{target}: not found")

def builtin_pwd(args):
    cwd = os.getcwd()
    print(cwd)
    return cwd

def builtin_cd(args):
    if len(args) == 0:
        target = get_env("HOME", os.path.expanduser("~"))
    elif len(args) == 1:
        target = args[0]
    else:
        print(f"cd: Invalid number of arguments. Expected 0 or 1, given {len(args)}")
        return

    if target.startswith("~"):
        target = os.path.expanduser(target)
    if not os.path.isabs(target):
        target = os.path.join(os.getcwd(), target)

    if not os.path.exists(target):
        print(f"cd: {target}: No such file or directory")
        return
    if not os.path.isdir(target):
        print(f"cd: {target}: Cannot cd into a file")
        return
    try:
        os.chdir(target)
        os.environ["PWD"] = target  # Update environment
    except Exception as e:
        print(f"cd: {target}: {e}")

# Register builtins
shell_builtins = {
    "echo": builtin_echo,
    "exit": builtin_exit,
    "type": builtin_type,
    "pwd": builtin_pwd,
    "cd": builtin_cd,
}

# ----------------------
# Redirection helpers
# ----------------------
@contextmanager
def redirect_stdout_to(fobj):
    orig = sys.stdout
    sys.stdout = fobj
    try:
        yield
    finally:
        sys.stdout = orig

@contextmanager
def redirect_stderr_to(fobj):
    orig = sys.stderr
    sys.stderr = fobj
    try:
        yield
    finally:
        sys.stderr = orig

@contextmanager
def redirect_stdin_to(fobj):
    orig = sys.stdin
    sys.stdin = fobj
    try:
        yield
    finally:
        sys.stdin = orig

def parse_redirections(tokens):
    """Parse redirection tokens and return (clean_tokens, stdout_info, stderr_info, stdin_info)."""
    stdout_info = None
    stderr_info = None
    stdin_info = None
    cleaned = []
    i = 0

    while i < len(tokens):
        t = tokens[i]
        handled = False
        fname = None
        mode = None
        advance = 0

        def next_token():
            return tokens[i + 1] if i + 1 < len(tokens) else None

        # Output redirection
        if t.startswith("1>>") or t.startswith(">>"):
            fname = t.split(">>", 1)[1] or next_token()
            mode = "a"
            stdout_info = (fname, mode)
            handled = True
            advance = 1 if not t.split(">>", 1)[1] else 0
        elif t.startswith("1>") or t.startswith(">"):
            fname = t.split(">", 1)[1] or next_token()
            mode = "w"
            stdout_info = (fname, mode)
            handled = True
            advance = 1 if not t.split(">", 1)[1] else 0
        elif t.startswith("2>>"):
            fname = t[3:] or next_token()
            mode = "a"
            stderr_info = (fname, mode)
            handled = True
            advance = 1 if not t[3:] else 0
        elif t.startswith("2>"):
            fname = t[2:] or next_token()
            mode = "w"
            stderr_info = (fname, mode)
            handled = True
            advance = 1 if not t[2:] else 0
        # Input redirection
        elif t.startswith("<"):
            fname = t[1:] or next_token()
            mode = "r"
            stdin_info = (fname, mode)
            handled = True
            advance = 1 if not t[1:] else 0
        # Separate tokens
        elif t in (">", ">>", "1>", "1>>", "2>", "2>>", "<"):
            fname = next_token()
            if t in (">", "1>"):
                stdout_info = (fname, "w")
            elif t in (">>", "1>>"):
                stdout_info = (fname, "a")
            elif t == "2>":
                stderr_info = (fname, "w")
            elif t == "2>>":
                stderr_info = (fname, "a")
            elif t == "<":
                stdin_info = (fname, "r")
            handled = True
            advance = 1

        if handled:
            i += 1 + advance
        else:
            cleaned.append(t)
            i += 1

    # Validate filenames
    for info, name in ((stdout_info, "stdout"), (stderr_info, "stderr"), (stdin_info, "stdin")):
        if info is not None and (info[0] is None or info[0] == ""):
            print(f"Error: no filename provided for {name} redirection")
            return cleaned, None, None, None

    return cleaned, stdout_info, stderr_info, stdin_info

# ----------------------
# Command execution
# ----------------------
def execute_command(cmd_name, args):
    """Execute a builtin or external command."""
    if cmd_name in shell_builtins:
        debug(f"Executing builtin: {cmd_name} {args}")
        try:
            shell_builtins[cmd_name](args)
        except SystemExit:
            raise
        except Exception as e:
            print(f"{cmd_name}: error: {e}", file=sys.stderr)
        return

    path = find_executable_in_path(cmd_name)
    if not path:
        print(f"{cmd_name}: command not found")
        return

    debug(f"Running external: {path} {args}")
    try:
        result = subprocess.run([path] + args, text=True)
        if result.returncode != 0:
            debug(f"Command exited with {result.returncode}")
    except FileNotFoundError:
        print(f"{cmd_name}: command not found")
    except Exception as e:
        print(f"{cmd_name}: error: {e}", file=sys.stderr)

# ----------------------
# Tab completion
# ----------------------
def completer(text, state):
    builtins = [b for b in shell_builtins if b.startswith(text)]
    paths = []
    if len(text) >= 2:
        for d in get_path_dirs():
            try:
                paths += [f for f in os.listdir(d) if f.startswith(text)]
            except FileNotFoundError:
                continue
    options = [o + " " for o in set(builtins + paths)]
    return options[state] if state < len(options) else None

# ----------------------
# Main REPL
# ----------------------
def main():
    try:
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass

    while True:
        try:
            sys.stdout.write("$ ")
            sys.stdout.flush()
            line = input()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue

        if not line.strip():
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as e:
            print(f"parse error: {e}")
            continue

        if not tokens:
            continue

        cmd_name, *args = tokens
        args_clean, stdout_info, stderr_info, stdin_info = parse_redirections(args)

        contexts = []
        files_to_close = []
        try:
            # stdout
            if stdout_info:
                fname, mode = stdout_info
                try:
                    f = open(fname, mode)
                    contexts.append(redirect_stdout_to(f))
                    files_to_close.append(f)
                except Exception as e:
                    print(f"Error opening {fname} for stdout redirection: {e}", file=sys.stderr)
                    continue

            # stderr
            if stderr_info:
                fname, mode = stderr_info
                try:
                    f = open(fname, mode)
                    contexts.append(redirect_stderr_to(f))
                    files_to_close.append(f)
                except Exception as e:
                    print(f"Error opening {fname} for stderr redirection: {e}", file=sys.stderr)
                    continue

            # stdin
            if stdin_info:
                fname, mode = stdin_info
                try:
                    f = open(fname, mode)
                    contexts.append(redirect_stdin_to(f))
                    files_to_close.append(f)
                except Exception as e:
                    print(f"Error opening {fname} for stdin redirection: {e}", file=sys.stderr)
                    continue

            with ExitStack() as stack:
                for ctx in contexts:
                    stack.enter_context(ctx)
                try:
                    execute_command(cmd_name, args_clean)
                except SystemExit:
                    for ff in files_to_close:
                        ff.close()
                    raise

        except SystemExit:
            break
        finally:
            for ff in files_to_close:
                try:
                    ff.close()
                except Exception:
                    pass

    debug("Shell exiting.")

if __name__ == "__main__":
    main()
