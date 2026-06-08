# bash completion for lchroot.
#
# lchroot is invoked as:  lchroot <osimage> [command...]
# The first argument is an osimage name; the rest is the command run inside the
# chroot. This completer offers osimage names for the first argument, sourced
# from the same authoritative place lchroot itself resolves them: the luna2
# daemon API, using the credentials/endpoint in luna.ini. So what completes and
# what lchroot will actually accept stay in agreement. For the in-image command
# it falls back to command-name (then filename) completion.
#
# If the daemon is unreachable, or curl/jq are missing, osimage completion is
# simply empty (it never errors or blocks beyond a short timeout).
#
# Install: shipped under utils/addons/ as <name>_completion.sh; the TrinityX luna
# role discovers every utils/addons/*_completion.sh and installs it to
# /etc/bash_completion.d/<name>.sh (here: lchroot.sh). To install manually, copy
# this file to /etc/bash_completion.d/lchroot.sh or `source` it from ~/.bashrc.
# Override the config path with LUNA_INI if it is not at the default location.

_lchroot_images() {
    local ini="${LUNA_INI:-/trinity/local/luna/utils/config/luna.ini}"
    command -v curl >/dev/null 2>&1 || return 0
    command -v jq   >/dev/null 2>&1 || return 0
    [ -r "$ini" ] || return 0

    # cache the list briefly so repeated TABs don't hammer the daemon
    local cache="${TMPDIR:-/tmp}/.lchroot_images.$(id -u)"
    if [ -f "$cache" ]; then
        local age
        age=$(( $(date +%s) - $(stat -c %Y "$cache" 2>/dev/null || echo 0) ))
        if [ "$age" -ge 0 ] && [ "$age" -lt 15 ]; then
            cat "$cache"
            return 0
        fi
    fi

    # parse the [API] section of luna.ini in a subshell (keeps the user's env clean)
    local names
    names=$(
        section="" endpoint="" proto="" user="" pass="" verify=""
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in ''|\#*|\;*) continue ;; esac
            if [[ "$line" =~ ^\[(.*)\][[:space:]]*$ ]]; then
                section="${BASH_REMATCH[1]}"
                continue
            fi
            [[ "$line" == *=* ]] || continue
            local k="${line%%=*}" v="${line#*=}"
            k="${k//[[:space:]]/}"
            v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
            if [ "$section" = "API" ]; then
                case "$k" in
                    ENDPOINT) endpoint="$v" ;;
                    PROTOCOL) proto="$v" ;;
                    USERNAME) user="$v" ;;
                    PASSWORD) pass="$v" ;;
                    VERIFY_CERTIFICATE) verify="$v" ;;
                esac
            fi
        done < "$ini"
        [ -n "$endpoint" ] && [ -n "$proto" ] || exit 0

        local insecure=""
        case "$(printf '%s' "$verify" | tr 'A-Z' 'a-z')" in
            false|no) insecure="--insecure" ;;
        esac

        local token
        token=$(curl $insecure --max-time 2 -s -X POST \
                    -H "Content-Type: application/json" \
                    -d "{\"username\":\"$user\", \"password\":\"$pass\"}" \
                    "${proto}://${endpoint}/token" 2>/dev/null \
                | jq -r '.token // empty' 2>/dev/null)
        [ -n "$token" ] || exit 0

        curl $insecure --max-time 2 -s -H "x-access-tokens: $token" \
             "${proto}://${endpoint}/config/osimage" 2>/dev/null \
            | jq -r '.config.osimage | keys[]' 2>/dev/null
    )

    [ -n "$names" ] || return 0
    printf '%s\n' "$names" > "$cache" 2>/dev/null
    printf '%s\n' "$names"
}

_lchroot() {
    local cur
    cur="${COMP_WORDS[COMP_CWORD]}"
    COMPREPLY=()

    if [ "$COMP_CWORD" -eq 1 ]; then
        # first argument: the osimage name
        mapfile -t COMPREPLY < <(compgen -W "$(_lchroot_images)" -- "$cur")
    elif [ "$COMP_CWORD" -eq 2 ]; then
        # the command to run inside the chroot
        mapfile -t COMPREPLY < <(compgen -c -- "$cur")
    else
        # arguments to that command
        mapfile -t COMPREPLY < <(compgen -o default -- "$cur")
    fi
}

complete -F _lchroot lchroot
