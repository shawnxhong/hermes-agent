# Only used by the dedicated Hermes terminal; never sourced by ordinary shells.
[[ -f ~/.bashrc ]] && source ~/.bashrc
export PATH="$HOME/.local/bin:$PATH"
set -o history
shopt -s histappend
_hermes_startup_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

_hermes_first_prompt() {
    # Remove ourselves BEFORE any blocking operation, including Ctrl+C paths.
    PROMPT_COMMAND=("${PROMPT_COMMAND[@]:1}")
    history -s 'hermes-box-voice start'
    history -a
    printf '\nStarting Hermes. After exit, press Up then Enter to start again.\n'
    if /usr/bin/python3 "$_hermes_startup_dir/desktop.py" prepare; then
        command hermes-box-voice start
    else
        printf '\nStartup did not finish. Check: journalctl --user -u hermes-production.service\n'
    fi
    printf '\nHermes command returned. Press Up then Enter to start again.\n'
}
PROMPT_COMMAND=(_hermes_first_prompt "${PROMPT_COMMAND[@]}")
