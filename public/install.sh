#!/usr/bin/env bash
# fxcss setup for macOS and Linux. Source: https://github.com/AdamXweb/fxcss
# Download and inspect: curl -fsSLo install-fxcss.sh https://fxcss.com/install.sh
# Run: bash install-fxcss.sh
# Piped use: curl -fsSL https://fxcss.com/install.sh | bash
# All installation work is inside main(), after the script has been parsed.

fxcss_setup() (
  set -euo pipefail
  local assume_yes=false intent='' setup_tmp='' pipx_cmd='' python_cmd='' platform=''

  say() { printf '%s\n' "$*"; }
  fail() { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }
  trap 'if [[ -n "$setup_tmp" ]]; then rm -rf -- "$setup_tmp"; fi' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  usage() {
    cat <<'HELP'
fxcss setup — install the Firefox theme toolkit through pipx

Usage: bash install.sh [--yes] [--intent use|build|maintain|install]

  --yes       Install without prompts (defaults to just installing).
  --intent    Choose the next-step guide without the menu.
  --help      Show this help without installing anything.

Interactive piping reads prompts from your terminal, not the downloaded script:
  curl -fsSL https://fxcss.com/install.sh | bash

For unattended setup, download first so download failures stop execution:
  curl -fsSLo install-fxcss.sh https://fxcss.com/install.sh && \
    bash install-fxcss.sh --yes --intent build

macOS and Linux are supported. Run as your normal user, without sudo.
If pipx is missing, setup uses Homebrew on macOS when available, otherwise
Python 3.10+ with venv. fxcss itself supports Python 3.9+ with existing pipx.
Firefox must be installed separately before using browser commands.
HELP
  }
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h) usage; return 0 ;;
      --yes|-y) assume_yes=true; shift ;;
      --intent)
        [[ $# -ge 2 ]] || fail '--intent needs use, build, maintain, or install.'
        intent=$2; shift 2 ;;
      *) fail "Unknown option: $1. Use --help for usage." ;;
    esac
  done
  case "$intent" in ''|use|build|maintain|install) ;; *) fail 'Unknown intent. Choose use, build, maintain, or install.' ;; esac

  if ! "$assume_yes"; then
    if [[ -t 0 ]]; then
      exec 3<&0
    elif { exec 3</dev/tty; } 2>/dev/null; then
      :
    else
      fail 'No interactive terminal. Download the script, then run bash install.sh --yes --intent install.'
    fi
  fi
  say 'fxcss — make Firefox your own'
  if [[ -z "$intent" ]]; then
    if "$assume_yes"; then intent=install
    else
      say ''
      say 'What would you like to do?'
      say '  1) Try a theme'
      say '  2) Build a theme'
      say '  3) Maintain a theme'
      say '  4) Just install fxcss'
      say '  0) Cancel'
      while :; do
        printf '\nChoose [1]: '
        local answer=''
        IFS= read -r answer <&3 || fail 'No answer received; nothing installed.'
        case "$answer" in
          ''|1) intent=use; break ;;
          2) intent=build; break ;;
          3) intent=maintain; break ;;
          4) intent=install; break ;;
          0) say 'Cancelled. Nothing installed.'; return 0 ;;
          *) say 'Choose a number from 0 to 4.' ;;
        esac
      done
    fi
  fi
  platform=$(uname -s)
  case "$platform" in
    Darwin|Linux) ;;
    *) fail 'This shell installer supports macOS and Linux. For Windows, see https://fxcss.com/docs/installation.' ;;
  esac
  [[ $(id -u) != 0 ]] || fail 'Run this script as your normal user, without sudo.'

  say ''
  say 'Setup will install pipx if needed, install fxcss with its image tools,'
  say 'and ask pipx to add its commands to your shell PATH.'
  say 'Existing pipx installations of fxcss keep their current version.'
  if ! "$assume_yes"; then
    printf '\nContinue? [Y/n]: '
    local answer=''
    IFS= read -r answer <&3 || fail 'No answer received; nothing installed.'
    case "$answer" in
      ''|y|Y|yes|YES) ;;
      *) say 'Cancelled. Nothing installed.'; return 0 ;;
    esac
  fi

  if command -v pipx >/dev/null 2>&1; then
    pipx_cmd=$(command -v pipx)
  elif [[ -x "${PIPX_BIN_DIR:-$HOME/.local/bin}/pipx" ]]; then
    pipx_cmd="${PIPX_BIN_DIR:-$HOME/.local/bin}/pipx"
  elif [[ "$platform" == Darwin ]] && command -v brew >/dev/null 2>&1; then
    say 'Installing pipx through Homebrew…'
    brew install pipx || fail 'Homebrew could not install pipx. Resolve the error above and run setup again.'
    pipx_cmd="$(brew --prefix)/bin/pipx"
  else
    local candidate
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
      if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        python_cmd=$(command -v "$candidate"); break
      fi
    done
    [[ -n "$python_cmd" ]] || fail 'Install Python 3.10+ or pipx, then rerun setup. See https://fxcss.com/docs/installation.'
    say 'Installing pipx in its own user environment…'
    setup_tmp=$(mktemp -d "${TMPDIR:-/tmp}/fxcss-setup.XXXXXX")
    "$python_cmd" -m venv "$setup_tmp/bootstrap" || fail 'Python could not create an environment. On Debian/Ubuntu, install python3-venv, then rerun setup.'
    "$setup_tmp/bootstrap/bin/python" -m pip install --disable-pip-version-check pipx || fail 'Could not download pipx. Check the error above and retry.'
    local bootstrap_pipx="$setup_tmp/bootstrap/bin/pipx" app_bin
    app_bin=$("$bootstrap_pipx" environment --value PIPX_BIN_DIR) || fail 'Could not locate the pipx app directory.'
    [[ "$app_bin" == /* ]] || fail 'pipx returned an invalid app directory.'
    "$bootstrap_pipx" install pipx --python "$python_cmd" || fail 'Could not install the persistent pipx command.'
    pipx_cmd="$app_bin/pipx"
  fi

  "$pipx_cmd" --version >/dev/null || fail 'The pipx command is unavailable. Fix pipx, then rerun setup.'
  if "$pipx_cmd" runpip fxcss show fxcss >/dev/null 2>&1; then
    say 'Using your existing pipx installation of fxcss.'
    if ! "$pipx_cmd" runpip fxcss show Pillow >/dev/null 2>&1; then
      "$pipx_cmd" inject fxcss 'pillow>=10.1' || fail 'Could not add the image tools to fxcss.'
    fi
  else
    "$pipx_cmd" install 'fxcss[images]' || fail 'Could not install fxcss. Check the error above and retry.'
  fi
  local app_bin fxcss_cmd
  app_bin=$("$pipx_cmd" environment --value PIPX_BIN_DIR) || fail 'Could not locate the installed fxcss command.'
  [[ "$app_bin" == /* ]] || fail 'pipx returned an invalid app directory.'
  fxcss_cmd="$app_bin/fxcss"
  "$fxcss_cmd" --version || fail 'Installation finished, but the fxcss command could not run.'
  if ! "$pipx_cmd" ensurepath; then
    say "Add this directory to your shell PATH: $app_bin"
  fi

  say ''
  say 'fxcss is installed. Open a new terminal so PATH changes take effect.'
  say 'Make sure Firefox is installed, then try your next steps:'
  say ''
  case "$intent" in
    use)
      say '  fxcss try AdamXweb/WhiteSurFirefoxThemeMacOS'
      say ''
      say 'This opens a disposable preview. Close it when you are done.'
      say 'Guide: https://fxcss.com/docs/try' ;;
    build)
      say '  fxcss new my-theme'
      say '  cd my-theme'
      say '  fxcss watch'
      say ''
      say 'Save an edit to chrome/userChrome.css to see it in Firefox.'
      say 'Guide: https://fxcss.com/docs/watch' ;;
    maintain)
      say 'From your theme repository:'
      say '  fxcss init --watch --showcase'
      say ''
      say 'Review the generated workflows before committing them.'
      say 'Guide: https://fxcss.com/docs/init' ;;
    install)
      say '  fxcss --help'
      say '  fxcss doctor'
      say ''
      say 'Guide: https://fxcss.com/docs' ;;
  esac
  say ''
  say 'To update later: pipx upgrade fxcss'
)

fxcss_setup "$@"
