# bash completion for lchroot.
#
# Image names are sourced from luna (the authoritative source) via
# `lchroot --list-images` on each TAB -- the same API-backed approach luna's own
# argcomplete completer uses, but through lchroot's typed client (no argcomplete
# dependency). If the luna daemon is unreachable, --list-images prints nothing and
# completion is simply empty (matching luna's behaviour). lchroot's actual resolution
# is luna-authoritative too, so completion and execution agree.
#
# Install: copy to /etc/bash_completion.d/lchroot (or
#   /usr/share/bash-completion/completions/lchroot), or `source` it from ~/.bashrc.
# Override the binary with LCHROOT_BIN if it is not on PATH as `lchroot`.

_lchroot() {
    local cur flags bin i
    cur="${COMP_WORDS[COMP_CWORD]}"
    bin="${LCHROOT_BIN:-lchroot}"
    flags="--ro --dry-run --status --force --path --hostname --no-emulate --list-images --verbose --debug --log-file --help"

    # completing a flag
    if [[ "$cur" == -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$flags" -- "$cur")
        return
    fi

    # if a non-flag word already precedes the cursor, the osimage is chosen and we are
    # now completing the in-image command -> fall back to command names.
    for ((i = 1; i < COMP_CWORD; i++)); do
        # skip the value of an option that takes one (it is not the osimage)
        case "${COMP_WORDS[i - 1]}" in
            --log-file | --hostname | --path) continue ;;
        esac
        if [[ "${COMP_WORDS[i]}" != -* ]]; then
            mapfile -t COMPREPLY < <(compgen -c -- "$cur")
            return
        fi
    done

    # completing the osimage name: ask luna (authoritative), via lchroot's own client
    mapfile -t COMPREPLY < <(compgen -W "$("$bin" --list-images 2>/dev/null)" -- "$cur")
}
complete -F _lchroot lchroot
