# bash completion for lconsole.
#
# lconsole is invoked as:  lconsole [options] <nodename>
# The positional argument is a compute node name; this completer offers node
# names for it, sourced from the same authoritative place lconsole itself
# resolves them: the luna2 daemon API, using the credentials/endpoint in
# luna.ini. So what completes and what lconsole will actually accept stay in
# agreement. Options complete from the parser's own set; --sol-backend
# completes its two choices.
#
# If the daemon is unreachable, or curl/jq are missing, node completion is
# simply empty (it never errors or blocks beyond a short timeout).
#
# Install: shipped under utils/addons/ as <name>_completion.sh; the TrinityX luna
# role discovers every utils/addons/*_completion.sh and installs it to
# /etc/bash_completion.d/<name>.sh (here: lconsole.sh). To install manually, copy
# this file to /etc/bash_completion.d/lconsole.sh or `source` it from ~/.bashrc.
# Override the config path with LUNA_INI if it is not at the default location.

_lconsole_nodes() {
    local ini="${LUNA_INI:-/trinity/local/luna/utils/config/luna.ini}"
    command -v curl >/dev/null 2>&1 || return 0
    command -v jq   >/dev/null 2>&1 || return 0
    [ -r "$ini" ] || return 0

    # cache the list briefly so repeated TABs don't hammer the daemon
    local cache="${TMPDIR:-/tmp}/.lconsole_nodes.$(id -u)"
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
             "${proto}://${endpoint}/config/node" 2>/dev/null \
            | jq -r '.config.node | keys[]' 2>/dev/null
    )

    [ -n "$names" ] || return 0
    printf '%s\n' "$names" > "$cache" 2>/dev/null
    printf '%s\n' "$names"
}

_lconsole() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    COMPREPLY=()

    case "$prev" in
        --sol-backend)
            mapfile -t COMPREPLY < <(compgen -W "ipmi redfish" -- "$cur")
            return
            ;;
        --sol-cipher|--sol-fail-grace|--sol-escape)
            # these take a free-form value; nothing sensible to offer
            return
            ;;
    esac

    if [[ "$cur" == -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "--sol-backend --sol-cipher --sol-fail-grace --sol-escape --debug --help" -- "$cur")
        return
    fi

    # positional: the node name (options may appear before it)
    mapfile -t COMPREPLY < <(compgen -W "$(_lconsole_nodes)" -- "$cur")
}

complete -F _lconsole lconsole
