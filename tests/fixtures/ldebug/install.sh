#!/bin/bash


export LUNA_IMAGEFILE=''
export LUNA_TOKEN="fixture.token.value"
export DECODE=""
## ----------- useful variable exports -----------
export _HOSTNAME="node001"
export _SYSTEMROOT="/sysroot"
export _BOOTIF=${LUNA_BOOTIF}
## ----------- rootmnt for scripts ---------------
if [[ -z ${rootmnt:-} ]]; then
    export rootmnt="/sysroot"
fi
## -----------------------------------------------

export INSECURE='--insecure'

if [ -n "${LUNA_BOOTIF}" ]; then
    export INTERFACE="--interface ${LUNA_BOOTIF}"
fi
if [ "$(echo "10.141.255.254" | grep ':')" ]; then
    export LUNA_URL="http://[10.141.255.254]:7050"
else
    export LUNA_URL="http://10.141.255.254:7050"
fi

base64 --help &> /dev/null
if [ "$?" == "0" ]; then
    DECODE=1
fi
if [ ! "$DECODE" ]; then
    echo "Luna2: --WARNING-- base64 decoder not available. This will impair the installer"
fi

function update_status {
    json='{"monitor": { "status": { "node001": { "state": "'$1'" } } } }'
    curl $INTERFACE $INSECURE -X POST -H "x-access-tokens: $LUNA_TOKEN" -H "Content-Type: application/json" -d "$json" -s "${LUNA_URL}/monitor/node/node001"
}

function update_node_ip {
    json='{"config": {"node": {"node001": {"name": "node001", "interfaces": [{"interface": "BOOTIF", "force": true, "ipaddress": "'$1'"}]}}}}'
    curl $INTERFACE $INSECURE -X POST -H "x-access-tokens: $LUNA_TOKEN" -H "Content-Type: application/json" -d "$json" -s "${LUNA_URL}/config/node/node001"
}

function update_inventory {
    # In-band hardware discovery (TRIX-1750): collect facts and report them to the
    # node inventory endpoint. Best-effort: every probe is guarded so a missing tool
    # never stops the install.
    local manufacturer='' product='' serial='' biosversion='' cpumodel='' cpucount=0 memmb=0 disks='' gpus='' nics=''
    dmidecode --help &> /dev/null
    if [ "$?" == "0" ]; then
        manufacturer=$(dmidecode -s system-manufacturer 2>/dev/null | awk 'NR==1{gsub(/"/,"");print;exit}')
        product=$(dmidecode -s system-product-name 2>/dev/null | awk 'NR==1{gsub(/"/,"");print;exit}')
        serial=$(dmidecode -s system-serial-number 2>/dev/null | awk 'NR==1{gsub(/"/,"");print;exit}')
        biosversion=$(dmidecode -s bios-version 2>/dev/null | awk 'NR==1{gsub(/"/,"");print;exit}')
    fi
    if [ -f /proc/cpuinfo ]; then
        cpumodel=$(awk -F: '/model name/{sub(/^ +/,"",$2);gsub(/"/,"",$2);print $2;exit}' /proc/cpuinfo)
        cpucount=$(grep -c '^processor' /proc/cpuinfo)
    fi
    if [ -f /proc/meminfo ]; then
        memmb=$(( $(grep -m1 MemTotal /proc/meminfo | grep -oE '[0-9]+') / 1024 ))
    fi
    if command -v lsblk > /dev/null 2>&1; then
        disks=$(lsblk -bdno NAME,SIZE,TYPE,ROTA,MODEL 2>/dev/null | awk '
            $3=="disk"{
                if(c++) printf ",";
                gb=int($2/1000000000);
                type=($1 ~ /^nvme/)?"nvme":($4=="0"?"ssd":"hdd");
                model=""; for(i=5;i<=NF;i++){ model=model (i>5?" ":"") $i }; gsub(/"/,"",model);
                printf "{\"name\":\"%s\",\"size_gb\":%d,\"type\":\"%s\",\"model\":\"%s\"}",$1,gb,type,model
            }')
    fi
    if command -v lspci > /dev/null 2>&1; then
        gpus=$(lspci 2>/dev/null | awk '
            /VGA compatible|3D controller|Display controller/{
                if(c++) printf ",";
                idx=index($0,": "); desc=substr($0,idx+2); gsub(/"/,"",desc);
                printf "{\"busid\":\"%s\",\"model\":\"%s\"}",$1,desc
            }')
    fi
    # network interfaces: one JSON object per physical NIC (skip lo/virtual)
    for _ifpath in /sys/class/net/*; do
        [ -e "$_ifpath/device" ] || continue           # physical NICs only
        _if="${_ifpath##*/}"
        _mac=$(cat "$_ifpath/address" 2>/dev/null)
        _speed=$(cat "$_ifpath/speed" 2>/dev/null); echo "$_speed" | grep -qE '^[0-9]+$' || _speed=0
        [ -n "$nics" ] && nics="${nics},"
        nics="${nics}{\"name\":\"${_if}\",\"mac\":\"${_mac}\",\"speed_mbps\":${_speed},\"capabilities\":\"\"}"
    done
    json='{"config": {"node": {"node001": {"inventory": {"source": "inband", "manufacturer": "'"$manufacturer"'", "product": "'"$product"'", "serial": "'"$serial"'", "bios_version": "'"$biosversion"'", "cpu_model": "'"$cpumodel"'", "cpu_count": '"${cpucount:-0}"', "memory_mb": '"${memmb:-0}"', "disks": ['"$disks"'], "gpus": ['"$gpus"'], "nics": ['"$nics"']}}}}}'
    curl $INTERFACE $INSECURE -X POST -H "x-access-tokens: $LUNA_TOKEN" -H "Content-Type: application/json" -d "$json" -s "${LUNA_URL}/config/node/node001/inventory"
}

function lunainit {
    if [ ! -d /lunatmp ]; then
        mkdir /lunatmp
    fi
    if [ ! -d "/${rootmnt}" ]; then
        echo "Luna2: Warning! ${rootmnt} did not exist! it's unexpected. I will create it and try to continue"
        mkdir -p "/${rootmnt}"
    fi
}

function dynamic_ip_check {
    if [ "${LUNA_BOOTPROTO}" == "dhcp" ]; then
        echo "Luna2: I need to update luna to reflect my dynamic IP address"
        if [ "${LUNA_BOOTIF}" ]; then
            MY_IP=$(ip a show dev ${LUNA_BOOTIF}|grep -oE "inet [0-9\.]+"|grep -oE "[0-9\.]+")
            if [ "${MY_IP}" ]; then
                update_node_ip $MY_IP
            else
                echo "Luna2: Could not figure out my IP address"
            fi
        else
            echo "Luna2: BOOTIF is not defined"
        fi
    fi
}

function unpack_imagefile {
    update_status "install.unpack"
    echo "Luna2: Un-packing imagefile"
    TAR_LOG_FILE=/tmp/tar.out
    if [ -f "${rootmnt}/compute-1779060427.tar.bz2" ]; then
        cd "/${rootmnt}"

        # We need the full /etc/{passwd,group} in order
        # to properly extract ACLs from the archive

        mv /etc/passwd /etc/passwd.back
        mv /etc/group /etc/group.back
        tar -I lbzip2 -xf ./compute-1779060427.tar.bz2 ./etc/passwd ./etc/group -C / -P
       
        if grep "^tmpfs" /proc/mounts | grep -q -e sysroot -e root -e "${rootmnt}"
        then
            tar -I lbzip2 --no-acls -xf ./compute-1779060427.tar.bz2 2> $TAR_LOG_FILE
            ret=$?
        else
            tar -I lbzip2 --acls -xf ./compute-1779060427.tar.bz2 2> $TAR_LOG_FILE
            ret=$?
        fi

        if [ "$ret" != "0" ]; then
            echo "Luna2: Error unpacking compute-1779060427.tar.bz2!"
            if [ -f $TAR_LOG_FILE ]; then
                echo "------------------------------------[head]-------------------------------------"
                awk 'NR<11' $TAR_LOG_FILE
                echo "-------------------------------------------------------------------------------"
                echo "Luna2: more details in $TAR_LOG_FILE"
            fi
            exit 1
        fi

        # Restore dracut's default /etc/{passwd,group}
        mv /etc/passwd.back /etc/passwd
        mv /etc/group.back /etc/group

        rm -f "/${rootmnt}/compute-1779060427.tar.bz2"
    else
        echo "Luna2: error downloading OsImage. Entering service mode."
        while true; do sleep 5 ;done
    fi
}



function customscript {
    if [ ! "" ]; then
        return 1
    fi
    if [ ! "$1" ]; then
        echo "Luna2: customerscript requires an argument"
        return 0
    fi
    local STAGE=$1
    local SCRIPTS=$(echo  | tr ',' ' ')
    for SCRIPT in $SCRIPTS; do
        if [ -f /lunatmp/scripts/${SCRIPT}-${STAGE}.sh ]; then
            bash /lunatmp/scripts/${SCRIPT}-${STAGE}.sh
            LUNARET=$?
            if [ "$LUNARET" != "0" ]; then
                echo "Luna2: ${SCRIPT} ${STAGE} script exited with exit code $LUNARET. Stop install."
                exit 1
            fi
        else
            echo "Luna2: Warning - ${SCRIPT}-${STAGE}.sh not found"
        fi
    done
}

function prescript {
    echo "Luna2: Running prescript"
    update_status "install.prescript"
cat << 'LUNAEOF' > /lunatmp/prescript.sh

## do not remove below line

    
LUNAEOF
    bash /lunatmp/prescript.sh
    LUNARET=$?
    if [ "$LUNARET" != "0" ]; then
        echo "Luna2: Pre script exited with exit code $LUNARET. Stop install."
        exit 1
    fi
    customscript pre
}

function write_provisioning_inputs {
    # Materialise the resolved Luna provisioning fields (install_mode + provision_*
    # + disklayout + osimage_filter) into the runtime dir the lpart tools read. Empty values are
    # not written -- an absent file means "not pinned". Runs after prescript so a
    # prescript that clears /tmp/install_files cannot wipe them, and before any
    # lpart phase, which reads them.
    echo "Luna2: Writing provisioning inputs"
    mkdir -p /tmp/install_files/runtime
    if [ -n "" ]; then
        echo "" | base64 -d > /tmp/install_files/runtime/storage.json
    fi
    if [ -n "" ]; then
        echo "" | base64 -d > /tmp/install_files/runtime/osimage-filter.json
    fi
    cat > /tmp/install_files/runtime/install-context.json <<LUNAEOF
{
  "install_mode": "auto",
  "provision_method": "http",
  "provision_fallback": "http",
  "provision_interface": "BOOTIF"
}
LUNAEOF
}


LPART_FALLBACK=0

function lpart_phase {
    # Native lpart invocation. The node's pre/part/post FIELDS stay the operator's;
    # this template calls lpart itself, so an operator script and the lpart pipeline
    # can never both partition the same disk.
    LUNAPHASE="$1"
    if [ "$LPART_FALLBACK" = "1" ]; then
        return 0
    fi
    if ! command -v lpart-node-installer > /dev/null 2>&1; then
        # An osimage that predates lpart. Decided HERE and not by the daemon: whether
        # this image can run lpart is a property of the initramfs the node booted, and
        # the daemon cannot see inside it. Report it, then install the classic way --
        # the classic functions are present and unmodified in this template, so the
        # fallback is the real legacy path, not an approximation of it.
        echo "==============================================================================="
        echo "Luna2: WARNING - lpart requested but not available on this osimage"
        echo "Luna2:   node            : node001"
        echo "Luna2:   install_mode    : auto"
        echo "Luna2:   missing         : lpart-node-installer"
        echo "Luna2:   FALLING BACK to the classic installer."
        echo "Luna2:   The disk layout will be whatever partscript produces, NOT the"
        echo "Luna2:   requested lpart layout."
        echo "Luna2:   Fix: rebuild the osimage with the lpart client, or set"
        echo "Luna2:        install_mode=legacy on this node to stop asking for it."
        echo "==============================================================================="
        # The message above is for the install console and /tmp/luna_install.log. This
        # state is the machine-readable half: it is short on purpose, because it is the
        # node's *state* field and the next step overwrites it -- but the controller logs
        # every state transition, so this line is what survives centrally.
        update_status "install.lpart_unavailable"
        LPART_FALLBACK=1
        return 0
    fi
    echo "Luna2: Running lpart ${LUNAPHASE} phase"
    update_status "install.lpart.${LUNAPHASE}"
    mkdir -p /tmp/install_files/logs
    lpart-node-installer --phase "${LUNAPHASE}" \
        --bin-dir "$(dirname "$(command -v lpart-node-installer)")" \
        --log-file "/tmp/install_files/logs/lpart-node-installer-${LUNAPHASE}.log"
    LUNARET=$?
    if [ "$LUNARET" != "0" ]; then
        echo "Luna2: lpart ${LUNAPHASE} phase exited with exit code $LUNARET. Stop install."
        exit $LUNARET
    fi
}


function partscript {
    echo "Luna2: Running partscript"
    update_status "install.partscript"
cat << 'LUNAEOF' > /lunatmp/partscript.sh
mount -t tmpfs tmpfs /sysroot

## do not remove below line

    
LUNAEOF
    bash /lunatmp/partscript.sh
    LUNARET=$?
    if [ "$LUNARET" != "0" ]; then
        echo "Luna2: Part script exited with exit code $LUNARET. Stop install."
        exit 1
    fi
    customscript part
}

function postscript {
    echo "Luna2: Running postscript"
    update_status "install.postscript"
cat << 'LUNAEOF' > /lunatmp/postscript.sh
echo 'tmpfs / tmpfs defaults 0 0' >> /sysroot/etc/fstab

## do not remove below line

    
LUNAEOF
    bash /lunatmp/postscript.sh
    LUNARET=$?
    if [ "$LUNARET" != "0" ]; then
        echo "Luna2: Post script exited with exit code $LUNARET. Stop install."
        exit 1
    fi
    customscript post
}

# our hardcoded way to run a post boot script.
# currently used for updating monitor node being 'booted'.
function postboot {
    if [ ! -d "/${rootmnt}/usr/local/sbin/" ]; then
        mkdir -p "/${rootmnt}/usr/local/sbin/"
    fi
## do not remove below lines
cat << 'LUNAEOF' > "/${rootmnt}/usr/local/sbin/postboot.sh"
#!/bin/bash


# -------------------------------------------------------
# loading and setting config
# -------------------------------------------------------

LUNA_INI='/trinity/local/node/config/luna.ini'
if [ -f $LUNA_INI ]; then
    while IFS='=' read -ra line; do
        if [ ! "$(echo $line | grep -E '^(#|;)')" ] && [ "$line" ]; then
            if [ "$(echo $line | grep '^\[')" ]; then
                section=$(echo $line | grep -oE "[A-Z]+")
            else
                key=$(echo ${line[0]})
                value=$(echo ${line[1]})
                declare -x "${section}_${key}"="$value"
            fi
        fi
    done < "$LUNA_INI"
else
    >&2 echo "No config file: $LUNA_INI"
fi

if [ "$API_PROTOCOL" == "" ]; then
    API_PROTOCOL=http
fi

if [ "$API_ENDPOINT" == "" ]; then
    LUNA_URL=http://10.141.255.254:7050
else
    LUNA_URL=${API_PROTOCOL}://${API_ENDPOINT}
fi

if [ "$API_USERNAME" == "" ]; then
    API_USERNAME=luna
fi
if [ "$API_PASSWORD" == "" ]; then
    API_PASSWORD=luna
fi

if [ "$API_VERIFY_CERTIFICATE" == "" ]; then
INSECURE='--insecure'

else
    API_VERIFY_CERTIFICATE=$(echo $API_VERIFY_CERTIFICATE | tr 'A-Z' 'a-z')
    if [ "$API_VERIFY_CERTIFICATE" == "false" ] || [ "$API_VERIFY_CERTIFICATE" == "no" ]; then
        INSECURE="--insecure"
    else
        INSECURE=''
    fi 
fi

HOST=$(hostname --short)
if [ ! "$HOST" ]; then # opensuse?
	HOST=$(hostname | awk -F'.' '{ print $1 }')
fi

# -------------------------------------------------------
# the actual calls
# -------------------------------------------------------

function fetch_token {
    export TPM2TOOLS_TCTI="device:/dev/tpm0"
    ldconfig
    TPM=$(tpm2_pcrread sha256:0 2> /dev/null | grep '0x' | awk -F' : ' '{ print $2 }')
    json='{"tpm_sha256":"'$TPM'","username":"'$API_USERNAME'","password":"'$API_PASSWORD'"}'
    touch /tmp/token.json
    chmod og-rwx /tmp/token.json
    curl $INSECURE -X POST -H "Content-Type: application/json" -d "$json" -s "${LUNA_URL}/tpm/${HOST}" > /tmp/token.json
    valid=$(grep token /tmp/token.json)
    if [ "$valid" ]; then
        token=$(cat /tmp/token.json | awk '{ print $2 }' | grep -oE '"(\S+)"')
        token=$(echo "${token:1:-1}")
        echo $token
    else
        >&2 cat /tmp/token.json
        >&2 echo
        echo "None"
    fi
}

function call_hook {
    LUNA_TOKEN=$(fetch_token)
    json='{"monitor": {"status": {"'"${HOST}"'": {"state": "install.booted"}}}}'
    curl $INSECURE -X POST -H "x-access-tokens: $LUNA_TOKEN" -H "Content-Type: application/json" -d "$json" -i -w "%{http_code}" -s "${LUNA_URL}//monitor/node/${HOST}" -o /tmp/post-boot.out > /tmp/post-boot.code
    RET=$?
    CODE=$(cat /tmp/post-boot.code)
    echo "$CODE :: $(cat /tmp/post-boot.out)"
    if [ "$CODE" != "200" ] && [ "$CODE" != "201" ] && [ "$CODE" != "204" ]; then
        return 3
    fi
    return $RET
}

TRY=8
INC=$(( ( RANDOM % 2 ) + 1 ))
while ! call_hook; do
    if [ "$TRY" -lt "0" ]; then
        echo "--> giving up"
        exit 2
    fi
    TRY=$(( TRY -1 ))
    INC=$(( INC * 2 ))
    VAR=$(( ( RANDOM % $INC )  + $INC ))
    echo "--> No success. We wait $VAR seconds before retrying...."
    sleep $VAR
done
exit $?


LUNAEOF
chmod 750 "/${rootmnt}/usr/local/sbin/postboot.sh" 2> /dev/null
cat << 'LUNAEOF' > "/${rootmnt}/etc/systemd/system/luna-post-boot.service"
[Unit]
Description=Luna post boot script
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/postboot.sh
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
LUNAEOF
chroot "/${rootmnt}" /bin/bash -c "systemctl enable luna-post-boot"
}

function get_encapsulated_content {
    FILE=$1
    if [ ! -s $FILE ]; then
        echo
        return 1
    fi
    CONTENT=$(cat $FILE)
    CONTENT=$(echo "${CONTENT:1:-1}")

    BASH_COMPAT=4.2
    CONTENT=$(echo "${CONTENT//\\\\/\\}")

    awk '{
      n = split($0, t, "\\\\n")
      for (i = 0; ++i <= n;)
#        print i, t[i]
        print t[i]
      }' <<< $CONTENT
}

function get_json_segment {
    FILE=$1
    SEGMENT=$2
    NODASH=$3
    if [ ! -s $FILE ]; then
        echo
        return 1
    fi
    if [ "$NODASH" ]; then
       cat $FILE | grep -oE '".[^"]+"'|grep -A1 $SEGMENT|grep -v '"'$SEGMENT'"'
    else
       cat $FILE | grep -oE '".[^"]+"'|grep -A1 $SEGMENT|grep -v '"'$SEGMENT'"' | grep -v '^\-\-'
    fi
}

function get_json_exact {
    FILE=$1
    KEY=$2
    if [ ! -s $FILE ]; then
        echo
        return 1
    fi
    cat $FILE | grep -oE '".[^"]+"' | grep -A1 '^"'$KEY'"$' | grep -v '^"'$KEY'"$' | grep -v '^\-\-$'
}

function node_secrets {
    update_status "install.secrets"

    curl $INTERFACE $INSECURE -H "x-access-tokens: $LUNA_TOKEN" -s "${LUNA_URL}/config/secrets/node/node001" | grep -v '"message":' > /lunatmp/node.secrets.json
    get_json_segment /lunatmp/node.secrets.json 'path' > /lunatmp/node.secrets.files.dat
    get_json_segment /lunatmp/node.secrets.json 'content' 'nodash' > /lunatmp/node.secrets.contents.dat
    get_json_exact /lunatmp/node.secrets.json 'resolved_owner' > /lunatmp/node.secrets.owners.dat
    get_json_exact /lunatmp/node.secrets.json 'mode' > /lunatmp/node.secrets.modes.dat

    TEL=0
    while IFS='' read -r line; do
        if [ "$(echo $line)" == "--" ]; then
           TEL=$[TEL+1]
       else
           echo $line >> /lunatmp/node.secrets.content${TEL}.dat
       fi
    done < /lunatmp/node.secrets.contents.dat

    TEL=0
    while IFS='' read -r file; do
        file=$(echo "${file:1:-1}")
        echo "Luna2: Node secret path: [$file]"
        if [ "$file" ]; then
            if [ "$DECODE" ]; then
                get_encapsulated_content /lunatmp/node.secrets.content${TEL}.dat | base64 -d > "/${rootmnt}/$file"
            else
                get_encapsulated_content /lunatmp/node.secrets.content${TEL}.dat > "/${rootmnt}/$file"
            fi
            owner=$(sed -n "$[TEL+1]p" /lunatmp/node.secrets.owners.dat)
            mode=$(sed -n "$[TEL+1]p" /lunatmp/node.secrets.modes.dat)
            if [ "$owner" ]; then owner=$(echo "${owner:1:-1}"); fi
            if [ "$mode" ]; then mode=$(echo "${mode:1:-1}"); fi
            chmod "${mode:-600}" "/${rootmnt}/$file" 2> /dev/null
            if [ "$owner" ] && [ "$owner" != "0:0" ]; then
                if ! chroot "/${rootmnt}" chown "$owner" "/$file" 2> /dev/null; then
                    echo "Luna2: --WARNING-- could not set owner [$owner] on secret [$file]"
                fi
            fi
            TEL=$[TEL+1]
        fi
    done < /lunatmp/node.secrets.files.dat
}

function node_mounts {
    update_status "install.mounts"

    # the mountpoints, with the owner, group and mode the document gives them
    echo "" | base64 -d | while read -r MPATH MOWNER MGROUP MMODE; do
        [ "$MPATH" ] || continue
        mkdir -p "/${rootmnt}${MPATH}"
        if [ "$MOWNER" != "-" ] || [ "$MGROUP" != "-" ]; then
            [ "$MOWNER" == "-" ] && MOWNER=""
            [ "$MGROUP" == "-" ] && MGROUP=""
            chroot "/${rootmnt}" /bin/chown "${MOWNER}:${MGROUP}" "${MPATH}" || echo "Luna2: mountpoint ${MPATH}: owner not set"
        fi
        [ "$MMODE" != "-" ] && chmod "$MMODE" "/${rootmnt}${MPATH}"
    done
    # the managed block replaces what an earlier install left; nothing outside it moves
    if [ -n "" ]; then
        touch "/${rootmnt}/etc/fstab"
        sed -i '/^# BEGIN luna mounts$/,/^# END luna mounts$/d' "/${rootmnt}/etc/fstab"
        # a mountpoint the image's own fstab already carries is left to that line:
        # a second line for one mountpoint breaks systemd's fstab generator
        echo "" | base64 -d | while read -r MLINE; do
            MPOINT=$(echo "$MLINE" | awk '/^[^#]/ {print $2}')
            if [ -n "$MPOINT" ] && awk -v mp="$MPOINT" '$1 !~ /^#/ && $2 == mp {found=1} END {exit !found}' "/${rootmnt}/etc/fstab"; then
                echo "Luna2: mountpoint ${MPOINT} already in fstab, left to the existing line"
            else
                echo "$MLINE" >> "/${rootmnt}/etc/fstab"
            fi
        done
    fi
    # what this node serves, for the nfs server the image carries
    if [ -n "" ]; then
        mkdir -p "/${rootmnt}/etc/exports.d"
        echo "" | base64 -d > "/${rootmnt}/etc/exports.d/luna.exports"
    fi
}

function node_roles {
    update_status "install.roles"

    if [ ! -d "/${rootmnt}/usr/local/roles" ]; then
        mkdir -p "/${rootmnt}/usr/local/roles"
    fi
    local ROLES=$(echo  | tr ',' ' ')
    for ROLE in $ROLES; do
        echo "Luna2: Preparing node role: [$ROLE]"
        curl $INTERFACE $INSECURE -H "x-access-tokens: $LUNA_TOKEN" -s "${LUNA_URL}/boot/roles/${ROLE}" | grep -v '"message":' > /lunatmp/node.roles.json
        get_json_segment /lunatmp/node.roles.json 'unit' > /lunatmp/node.roles.unit.dat
        get_json_segment /lunatmp/node.roles.json 'script' > /lunatmp/node.roles.script.dat

        if [ "$DECODE" ]; then
            get_encapsulated_content /lunatmp/node.roles.script.dat | base64 -d | awk '{if (NR==1 && NF==0) next};1' > "/${rootmnt}/usr/local/roles/${ROLE}"
            get_encapsulated_content /lunatmp/node.roles.unit.dat | base64 -d > "/${rootmnt}/etc/systemd/system/luna-${ROLE}.service"
        else
            get_encapsulated_content /lunatmp/node.roles.script.dat | awk '{if (NR==1 && NF==0) next};1' > "/${rootmnt}/usr/local/roles/${ROLE}"
            get_encapsulated_content /lunatmp/node.roles.unit.dat > "/${rootmnt}/etc/systemd/system/luna-${ROLE}.service"
        fi
        chmod 750 "/${rootmnt}/usr/local/roles/${ROLE}" 2> /dev/null
        chroot "/${rootmnt}" /bin/bash -c "systemctl enable luna-${ROLE}"
    done
}

function node_profiles {
    update_status "install.profiles"

    local PROFILES=$(echo  | tr ',' ' ')
    local PROFILE_STATE=/var/lib/luna/profiles
    local PROFILE_BACKUP=${PROFILE_STATE}/backup
    rm -f /lunatmp/node.profile.manifest.dat
    for PROFILE in $PROFILES; do
        echo "Luna2: Preparing node profile: [$PROFILE]"
        curl $INTERFACE $INSECURE -H "x-access-tokens: $LUNA_TOKEN" -s "${LUNA_URL}/boot/profiles/${PROFILE}" | grep -v '"message":' > /lunatmp/node.profile.json
        get_json_segment /lunatmp/node.profile.json 'path' > /lunatmp/node.profile.files.dat
        get_json_segment /lunatmp/node.profile.json 'content' 'nodash' > /lunatmp/node.profile.contents.dat
        get_json_exact /lunatmp/node.profile.json 'resolved_owner' > /lunatmp/node.profile.owners.dat
        get_json_exact /lunatmp/node.profile.json 'mode' > /lunatmp/node.profile.modes.dat
        SERVICE=$(get_json_exact /lunatmp/node.profile.json 'service')
        ACTION=$(get_json_exact /lunatmp/node.profile.json 'action')
        if [ "$SERVICE" ]; then SERVICE=$(echo "${SERVICE:1:-1}"); fi
        if [ "$ACTION" ]; then ACTION=$(echo "${ACTION:1:-1}"); fi

        # content[0-9]*, not content*: the second glob also matches
        # node.profile.contents.dat, which is the file the loop below reads
        rm -f /lunatmp/node.profile.content[0-9]*.dat
        TEL=0
        while IFS='' read -r line; do
            if [ "$(echo $line)" == "--" ]; then
               TEL=$[TEL+1]
           else
               echo $line >> /lunatmp/node.profile.content${TEL}.dat
           fi
        done < /lunatmp/node.profile.contents.dat

        TEL=0
        while IFS='' read -r file; do
            file=$(echo "${file:1:-1}")
            echo "Luna2: Profile ${PROFILE} file path: [$file]"
            if [ "$file" ]; then
                existed=false
                if [ -f "/${rootmnt}/$file" ]; then
                    mkdir -p "/${rootmnt}${PROFILE_BACKUP}/$(dirname $file)"
                    cp -a "/${rootmnt}/$file" "/${rootmnt}${PROFILE_BACKUP}/$file"
                    existed=true
                fi
                echo "  \"$file\": {\"profile\": \"${PROFILE}\", \"existed_before\": ${existed}, \"service\": \"${SERVICE}\", \"action\": \"${ACTION}\"}," >> /lunatmp/node.profile.manifest.dat
                mkdir -p "/${rootmnt}/$(dirname $file)"
                if [ "$DECODE" ]; then
                    get_encapsulated_content /lunatmp/node.profile.content${TEL}.dat | base64 -d > "/${rootmnt}/$file"
                else
                    get_encapsulated_content /lunatmp/node.profile.content${TEL}.dat > "/${rootmnt}/$file"
                fi
                owner=$(sed -n "$[TEL+1]p" /lunatmp/node.profile.owners.dat)
                mode=$(sed -n "$[TEL+1]p" /lunatmp/node.profile.modes.dat)
                if [ "$owner" ]; then owner=$(echo "${owner:1:-1}"); fi
                if [ "$mode" ]; then mode=$(echo "${mode:1:-1}"); fi
                chmod "${mode:-644}" "/${rootmnt}/$file" 2> /dev/null
                if [ "$owner" ] && [ "$owner" != "0:0" ]; then
                    if ! chroot "/${rootmnt}" chown "$owner" "/$file" 2> /dev/null; then
                        echo "Luna2: --WARNING-- could not set owner [$owner] on profile file [$file]"
                    fi
                fi
                TEL=$[TEL+1]
            fi
        done < /lunatmp/node.profile.files.dat

        SERVICE=$(get_json_exact /lunatmp/node.profile.json 'service')
        ACTION=$(get_json_exact /lunatmp/node.profile.json 'action')
        if [ "$SERVICE" ]; then SERVICE=$(echo "${SERVICE:1:-1}"); fi
        if [ "$ACTION" ]; then ACTION=$(echo "${ACTION:1:-1}"); fi
        if [ "$SERVICE" ]; then
            echo "Luna2: Profile ${PROFILE} service: [$SERVICE] action: [$ACTION]"
            case $ACTION in
                stop)
                    chroot "/${rootmnt}" /bin/bash -c "systemctl disable ${SERVICE}" 2> /dev/null
                    ;;
                none)
                    ;;
                *)
                    chroot "/${rootmnt}" /bin/bash -c "systemctl enable ${SERVICE}" 2> /dev/null
                    ;;
            esac
        fi
    done

    # the record the live applier picks up: what we wrote, and what was there
    # before we wrote it. without it a later delivery would treat these files as
    # pre-existing and keep the profile's own output as the thing to restore
    if [ -s /lunatmp/node.profile.manifest.dat ]; then
        mkdir -p "/${rootmnt}${PROFILE_STATE}"
        echo "{" > "/${rootmnt}${PROFILE_STATE}/manifest.json"
        sed '$ s/,$//' /lunatmp/node.profile.manifest.dat >> "/${rootmnt}${PROFILE_STATE}/manifest.json"
        echo "}" >> "/${rootmnt}${PROFILE_STATE}/manifest.json"
    fi
}

function node_scripts {
    update_status "install.scripts"

    if [ ! -d /lunatmp/scripts ]; then
        mkdir -p /lunatmp/scripts
    fi
    local SCRIPTS=$(echo  | tr ',' ' ')
    for SCRIPT in $SCRIPTS; do
        echo "Luna2: Preparing node script: [$SCRIPT]"
        curl $INTERFACE $INSECURE -H "x-access-tokens: $LUNA_TOKEN" -s "${LUNA_URL}/boot/scripts/${SCRIPT}" | grep -v '"message":' > /lunatmp/node.scripts.json
        get_json_segment /lunatmp/node.scripts.json 'prescript' > /lunatmp/node.scripts.pre.dat
        get_json_segment /lunatmp/node.scripts.json 'partscript' > /lunatmp/node.scripts.part.dat
        get_json_segment /lunatmp/node.scripts.json 'postscript' > /lunatmp/node.scripts.post.dat

        if [ "$DECODE" ]; then
            for item in pre part post; do
                get_encapsulated_content /lunatmp/node.scripts.${item}.dat | base64 -d | awk '{if (NR==1 && NF==0) next};1' > /lunatmp/scripts/${SCRIPT}-${item}.sh
            done
        else
            for item in pre part post; do
                get_encapsulated_content /lunatmp/node.scripts.${item}.dat | awk '{if (NR==1 && NF==0) next};1' > /lunatmp/scripts/${SCRIPT}-${item}.sh
            done
        fi
        chmod 750 /lunatmp/scripts/*.sh 2> /dev/null
    done
}

## this section contains the download function calls
## it will be rendered based on plugins
## do not remove below line
function download_http {

    echo "Luna2: Downloading imagefile http://10.141.255.254:7051/files/compute-1779060427.tar.bz2 to //sysroot/compute-1779060427.tar.bz2"
    if [ "$(echo "10.141.255.254" | grep ':')" ]; then
        curl $INTERFACE -H "x-access-tokens: $LUNA_TOKEN" -s http://[10.141.255.254]:7051/files/compute-1779060427.tar.bz2 > //sysroot/compute-1779060427.tar.bz2
    else
        curl $INTERFACE -H "x-access-tokens: $LUNA_TOKEN" -s http://10.141.255.254:7051/files/compute-1779060427.tar.bz2 > //sysroot/compute-1779060427.tar.bz2
    fi
    return $?
    
}
function download_http {

    echo "Luna2: Downloading imagefile http://10.141.255.254:7051/files/compute-1779060427.tar.bz2 to //sysroot/compute-1779060427.tar.bz2"
    if [ "$(echo "10.141.255.254" | grep ':')" ]; then
        curl $INTERFACE -H "x-access-tokens: $LUNA_TOKEN" -s http://[10.141.255.254]:7051/files/compute-1779060427.tar.bz2 > //sysroot/compute-1779060427.tar.bz2
    else
        curl $INTERFACE -H "x-access-tokens: $LUNA_TOKEN" -s http://10.141.255.254:7051/files/compute-1779060427.tar.bz2 > //sysroot/compute-1779060427.tar.bz2
    fi
    return $?
    
}
## FETCH CODE SEGMENT


function download_image {
    update_status "install.download"
    for method in http http; do
        case $method in
            http)
                if download_http; then
                    return 0
                fi
                ;;
            http)
                if download_http; then
                    return 0
                fi
                ;;
            *)
                echo "Luna2: No suitable provision method available for $method"
                ;;
        esac
    done
    return 1
}

# -------------------------------- bmc related functions ----------------------------

function config_bmc {
    : # the spliced segment below is the body; this keeps it valid bash when empty
## do not remove below line

    modprobe ipmi_devintf
    modprobe ipmi_si
    modprobe ipmi_msghandler
    sleep 2
    if ls /dev/ipmi* 1> /dev/null 2>&1
    then
        RESETIPMI=0
        IPMI_SET_MAX_ATTEMPTS=10
        IPMI_SET_RETRY_SLEEP=1

        refresh_ipmi_state() {
            IPMITOOL="$(ipmitool lan print ${NETCHANNEL} 2>/dev/null)"
            CUR_IPSRC="$(echo "${IPMITOOL}" | grep -e "^IP Address Source" | awk '{ print $5 }')"
            CUR_IPADDR="$(echo "${IPMITOOL}" | grep -e "^IP Address.*: [0-9]" | awk '{ print $4 }')"
            CUR_NETMASK="$(echo "${IPMITOOL}" | grep -e "^Subnet Mask" | awk '{ print $4 }')"
            CUR_DEFGW="$(echo "${IPMITOOL}" | grep -e "^Default Gateway IP" | awk '{ print $5 }')"
            CUR_VLANID="$(echo "${IPMITOOL}" | grep -e "^802.1q VLAN ID" | awk '{ print $5 }')"
        }

        get_current_ipmi_value() {
            case "$1" in
                ipsrc)
                    echo "${CUR_IPSRC}"
                    ;;
                ipaddr)
                    echo "${CUR_IPADDR}"
                    ;;
                netmask)
                    echo "${CUR_NETMASK}"
                    ;;
                defgw)
                    echo "${CUR_DEFGW}"
                    ;;
                vlan)
                    echo "${CUR_VLANID}"
                    ;;
            esac
        }

        get_expected_ipmi_value() {
            case "$1" in
                ipsrc)
                    # what the board will report back, which is not the word we
                    # set: ipmitool takes 'static'/'dhcp' and prints 'Static'/'DHCP'
                    if [ "${IPSRC}" == "dhcp" ]; then echo "DHCP"; else echo "Static"; fi
                    ;;
                ipaddr)
                    echo "${IPADDRESS}"
                    ;;
                netmask)
                    echo "${NETMASK}"
                    ;;
                defgw)
                    echo "${GATEWAY}"
                    ;;
                vlan)
                    echo "${VLANID}"
                    ;;
            esac
        }

        run_ipmi_command() {
            local FIELD="$1"
            local RUN_INDEX=1
            local RUN_COUNT=1
            local COMMAND_RC=0
            case "${FIELD}" in
                ipaddr)
                    RUN_COUNT=2
                    ;;
            esac
            while [[ "${RUN_INDEX}" -le "${RUN_COUNT}" ]]
            do
                case "${FIELD}" in
                    ipsrc)
                        ipmitool lan set ${NETCHANNEL} ipsrc ${IPSRC}
                        ;;
                    ipaddr)
                        ipmitool lan set ${NETCHANNEL} ipaddr ${IPADDRESS}
                        ;;
                    netmask)
                        ipmitool lan set ${NETCHANNEL} netmask ${NETMASK}
                        ;;
                    defgw)
                        ipmitool lan set ${NETCHANNEL} defgw ipaddr ${GATEWAY}
                        ;;
                    vlan)
                        if [ "$VLANID" == 'Disabled' ]; then
                            ipmitool lan set ${NETCHANNEL} vlan id off
                        else
                            ipmitool lan set ${NETCHANNEL} vlan id ${VLANID}
                        fi
                        ;;
                esac
                COMMAND_RC=$?
                if [[ "${COMMAND_RC}" -ne 0 ]]
                then
                    return ${COMMAND_RC}
                fi
                RUN_INDEX=$((RUN_INDEX+1))
            done
            return 0
        }

        set_ipmi_value() {
            local FIELD="$1"
            local EXPECTED="$(get_expected_ipmi_value "${FIELD}")"
            local ATTEMPT=1
            local CURRENT_VALUE=""
            local COMMAND_RC=0
            echo "Luna2: configuring BMC ${FIELD} to ${EXPECTED}"
            while [[ "${ATTEMPT}" -le "${IPMI_SET_MAX_ATTEMPTS}" ]]
            do
                echo "Luna2: applying ${FIELD} attempt ${ATTEMPT}/${IPMI_SET_MAX_ATTEMPTS}"
                run_ipmi_command "${FIELD}"
                COMMAND_RC=$?
                sleep "${IPMI_SET_RETRY_SLEEP}"
                refresh_ipmi_state
                CURRENT_VALUE="$(get_current_ipmi_value "${FIELD}")"
                if [[ "${CURRENT_VALUE}" == "${EXPECTED}" ]]
                then
                    echo "Luna2: BMC ${FIELD} now set to ${CURRENT_VALUE}"
                    return 0
                fi
                echo "Luna2: BMC ${FIELD} currently '${CURRENT_VALUE}', waiting for '${EXPECTED}'"
                if [[ "${COMMAND_RC}" -ne 0 ]]
                then
                    echo "Luna2: ipmitool command for ${FIELD} returned ${COMMAND_RC} on attempt ${ATTEMPT}/${IPMI_SET_MAX_ATTEMPTS}" >&2
                fi
                ATTEMPT=$((ATTEMPT+1))
            done
            echo "Luna2: failed to set IPMI ${FIELD} to ${EXPECTED} after ${IPMI_SET_MAX_ATTEMPTS} attempts; current value is ${CURRENT_VALUE}" >&2
            return 1
        }

        ensure_ipmi_value() {
            local FIELD="$1"
            local EXPECTED="$(get_expected_ipmi_value "${FIELD}")"
            local CURRENT_VALUE="$(get_current_ipmi_value "${FIELD}")"
            if [[ "${CURRENT_VALUE}" == "${EXPECTED}" ]]
            then
                echo "Luna2: BMC ${FIELD} already set to ${CURRENT_VALUE}"
                return 0
            fi
            RESETIPMI=1
            set_ipmi_value "${FIELD}"
        }

        if [[ "$VLANID" == "" ]]
        then
            VLANID='Disabled'
        fi

        # DHCP or a static address, and the difference is more than one setting:
        # with DHCP the address, netmask and gateway are the server's to hand out,
        # so setting them here would fight whatever it leases. The daemon only
        # asks for DHCP where the BMC's network actually serves it - a BMC set to
        # DHCP with nothing answering does not fall back, it simply has no address.
        IPSRC=static
        if [ "${DHCP}" == "True" ] || [ "${DHCP}" == "1" ]; then
            IPSRC=dhcp
        fi

        echo "Luna2: starting BMC configuration on net channel ${NETCHANNEL} (ipsrc ${IPSRC})"
        refresh_ipmi_state

        ensure_ipmi_value ipsrc || return 1
        if [ "${IPSRC}" == "static" ]; then
            ensure_ipmi_value ipaddr || return 1
            ensure_ipmi_value netmask || return 1
            ensure_ipmi_value defgw || return 1
        fi
        ensure_ipmi_value vlan || return 1

        case $UNMANAGED in
            disable)
                echo "Luna2: disabling unmanaged BMC users mode=${UNMANAGED}"
                for userid in $(ipmitool user list 1|grep -oE '^[0-9]+\s{1,10}.[^ ]+'|grep -oE '^[0-9]+'); do
                    ipmitool user disable $userid
                done
                ;;
            delete)
                echo "Luna2: disabling unmanaged BMC users mode=${UNMANAGED}. deletion pending feature"
                for userid in $(ipmitool user list 1|grep -oE '^[0-9]+\s{1,10}.[^ ]+'|grep -oE '^[0-9]+'); do
                    ipmitool user disable $userid
                done
                ;;
        esac
        echo "Luna2: configuring BMC user id ${USERID} name ${USERNAME} on management channel ${MGMTCHANNEL}"
        ipmitool user set name ${USERID} "${USERNAME}"
        echo "Luna2: setting BMC password for user id ${USERID} (value hidden)"
        ipmitool user set password ${USERID} "${PASSWORD}"
        echo "Luna2: enabling BMC channel access for user id ${USERID} on management channel ${MGMTCHANNEL}"
        ipmitool channel setaccess ${MGMTCHANNEL} ${USERID} link=on ipmi=on callin=on privilege=4
        echo "Luna2: enabling BMC user id ${USERID}"
        ipmitool user enable ${USERID}
        if [[ "${RESETIPMI}" == "1" ]]
        then
            echo "Luna2: issuing BMC cold reset"
            ipmitool mc reset cold
        fi
    else
        echo "Luna2: BMC not ready or not available - skipping config"
    fi
    
}

function bmcsetup {
    echo "Luna2: bmcsetup"
    update_status "install.setupbmc"
    NETCHANNEL="1"
    IPADDRESS="10.148.0.3"
    NETMASK="255.255.0.0"
    GATEWAY="0.0.0.0"
    VLANID=""
    DHCP="False"
    MGMTCHANNEL="1"
    USERID="2"
    USERNAME="admin"
    PASSWORD="fixture-bmc-pw"
    UNMANAGED="skip"
    config_bmc
}

function hold_for_daemon {
    # A restore owed by a firmware flash, or a BIOS push, scheduled for this node
    # resets it. The daemon decided at render time whether one is, and how long to
    # hold so that the reset lands here rather than in the middle of the install.
    # Fixed and bounded: nothing is asked of the daemon, and it ends regardless.
    if [ "0" -gt 0 ] 2>/dev/null; then
        echo "Luna2: nothing scheduled for this node and may reset it; holding 0s so that reset does not land mid-install"
        sleep 0
    fi
}


# -------------------------------- network related functions ----------------------------
function config_hostname {
    HOSTNAME=$1
    if [ ! -z $HOSTNAME ]; then
        echo "Luna2: set hostname"
        echo "$HOSTNAME" > /proc/sys/kernel/hostname
        if [ ! -z $rootmnt ]; then
            chroot $rootmnt /usr/bin/hostnamectl --static set-hostname $HOSTNAME
            echo "$HOSTNAME" > $rootmnt/etc/hostname
        else
            chroot /sysroot hostnamectl --static set-hostname $HOSTNAME 2> /dev/null
        fi
    else
        echo "Luna2: Hostname not defined and not set!"
    fi
}

function config_network_init {
    DEVICE=$1
    MTU=$2
    VLANPARENT=$3
    VLANID=$4
    TYPE=$5
    NETWORKTYPE=$6
    ZONE=$7
    OPTIONS=$8
## do not remove below line
## NETWORK INIT CODE SEGMENT
}

# ipv4
function config_interface {
    DEVICE=$1
    IPADDR=$2
    PREFIX=$3
    NETMASK=$4
    MTU=$5
    VLANPARENT=$6
    VLANID=$7
    TYPE=$8
    NETWORKTYPE=$9
    ZONE=${10}
    OPTIONS=${11}
## do not remove below line
## INTERFACE CODE SEGMENT
}

function config_gateway {
    DEVICE=$1
    GATEWAY=$2
    METRIC=$3
## do not remove below line
## GATEWAY CODE SEGMENT
}

function config_dns {
    DEVICE=$1
    SEARCH=$2
    NAMESERVER=$3
## do not remove below line
## DNS CODE SEGMENT
}

# ipv6
function config_interface_ipv6 {
    DEVICE=$1
    IPADDR=$2
    PREFIX=$3
    NETMASK=$4
    MTU=$5
    VLANPARENT=$6
    VLANID=$7
    TYPE=$8
    NETWORKTYPE=$9
    ZONE=${10}
    OPTIONS=${11}
## do not remove below line
## INTERFACE IPv6 CODE SEGMENT
}

function config_gateway_ipv6 {
    DEVICE=$1
    GATEWAY=$2
    METRIC=$3
## do not remove below line
## GATEWAY IPv6 CODE SEGMENT
}

function config_dns_ipv6 {
    DEVICE=$1
    SEARCH=$2
    NAMESERVER=$3
## do not remove below line
## DNS IPv6 CODE SEGMENT
}

function collect_mac_n_name_net {
    echo "Luna2: collecting interfaces and mac addresses"
    ip a 2> /dev/null|grep -oE "^[0-9]:\s*.[^:]+|^\s*link/ether [0-9a-fA-F:]+"|sed -e 's/^[0-9]: /~/g'|tr '\n' ' '|tr '~' '\n'|awk '{ print $1"="$3 }'|grep -v '=$' > /tmp/mac_n_name.dat
    if [ ! -s /tmp/mac_n_name.dat ]; then
        ifconfig -a 2>/dev/null |grep -oE "^.[^ :]+:|^\s*ether\s*[0-9a-fA-F:]+"|sed -e ':a;N;$!ba;s/:\n/ /g'|awk '{ print $1"="$3 }'|grep -v '=$' > /tmp/mac_n_name.dat
    fi
    if [ ! -s /tmp/mac_n_name.dat ]; then
        (for mac_interface in $(ls /sys/class/net 2>/dev/null); do echo -n ${mac_interface}'='; cat /sys/class/net/${mac_interface}/address; done)  > /tmp/mac_n_name.dat
    fi
}

function get_interface_by_mac {
    local get_interface_name=$1
    local get_interface_mac=$2
    if [ ! "$get_interface_name" ] || [ ! "$get_interface_mac" ]; then
        echo $get_interface_name
        return
    fi
    found_interface=$(grep -iw "$get_interface_mac" /tmp/mac_n_name.dat 2> /dev/null | grep -m1 . | awk -F'=' '{ print $1 }' | grep -v '^$' || echo $get_interface_name)
    echo "Luna2: get_interface_by_mac: $get_interface_name, $get_interface_mac -> $found_interface" 1>&2
    echo $found_interface
}

function change_net {
    echo "Luna2: apply network settings"
    update_status "install.setnet"
    config_hostname "node001.cluster"
    
    # ${LUNA_BOOTIF} variable should be defined in dracut environment
    # during the boot proces based on mac address: MAC
    if [ -n "${LUNA_BOOTIF}" ]; then
        interface_name=${LUNA_BOOTIF}
    else
        interface_name=BOOTIF
    fi
    interface_bootif=$interface_name
    echo "Luna2: BOOTIF: $interface_bootif"
    parent_name=
    ######### BOOTIF #########

      # yes, LUNAEOF without ticks.
      cat << LUNAEOF > "/lunatmp/interface_${interface_name}.conf"
## do not remove below line
#FILE /sysroot/etc/NetworkManager/system-connections/Connection_${interface_name}.nmconnection

[connection]
id=Connection_${interface_name}
type=ethernet
zone=trusted
interface-name=${interface_name}
autoconnect=true


[ethernet]



  
[ipv4]
method=manual
address1=10.141.0.11/16
dns=10.141.255.254
dns-search=cluster;extern
route1=0.0.0.0/0,10.141.255.254,101
    
[ipv6]
method=ignore

[proxy]



LUNAEOF

      if [ -s "/lunatmp/interface_${interface_name}.conf" ]; then
        interface_file=$(grep '^#FILE' "/lunatmp/interface_${interface_name}.conf"| awk -F'#FILE ' '{ print $2 }')
	if [ "${interface_file}" ]; then
            cp "/lunatmp/interface_${interface_name}.conf" ${interface_file}
            chown root:root ${interface_file}
            chmod 600 ${interface_file}
        fi
      if [ ! "${interface_file}" ]; then
        cat << 'LUNAEOF' > /lunatmp/interface_options.txt

LUNAEOF
        OPTIONS=$(cat /lunatmp/interface_options.txt)
        rm -f /lunatmp/interface_options.*
        # ---------------- INIT ---------------------------
        config_network_init "${interface_name}" "" "${parent_name}" "" "ethernet" "ethernet" "trusted" "${OPTIONS}"
        # ---------------- IPv4 ---------------------------
        if ([ "BOOTIF" == "BOOTIF" ] &&  [ "${LUNA_BOOTPROTO}" == "dhcp" ]) || [ "" ]; then
            echo "Using DHCP for IPv4 config"
            config_interface "${interface_name}" "dhcp"
        else
            # -- we call a function below that's being generated by interface plugin
            config_interface "${interface_name}" "10.141.0.11" "16" "255.255.0.0" "" "${parent_name}" ""  "ethernet" "ethernet" "trusted" "${OPTIONS}"
            config_gateway "${interface_name}" "10.141.255.254" "101"
        fi
        config_dns "${interface_name}" "cluster;extern" "10.141.255.254"
        # --
        # ---------------- IPv6 ---------------------------
        config_interface_ipv6 "${interface_name}" "linklocal"
      fi
    fi
    
    interface_name=eth0.1
    parent_name=$interface_bootif
    ######### eth0.1 #########

      # yes, LUNAEOF without ticks.
      cat << LUNAEOF > "/lunatmp/interface_${interface_name}.conf"
## do not remove below line
#FILE /sysroot/etc/NetworkManager/system-connections/Connection_${interface_name}.nmconnection

[connection]
id=Connection_${interface_name}
type=vlan
zone=trusted
interface-name=${interface_name}
autoconnect=true


[ethernet]


[vlan]
parent=${parent_name}
id=2

  
[ipv4]
method=manual
address1=10.145.0.104/16
route1=0.0.0.0/0,10.145.255.254,101
    
[ipv6]
method=ignore

[proxy]



LUNAEOF

      if [ -s "/lunatmp/interface_${interface_name}.conf" ]; then
        interface_file=$(grep '^#FILE' "/lunatmp/interface_${interface_name}.conf"| awk -F'#FILE ' '{ print $2 }')
	if [ "${interface_file}" ]; then
            cp "/lunatmp/interface_${interface_name}.conf" ${interface_file}
            chown root:root ${interface_file}
            chmod 600 ${interface_file}
        fi
      if [ ! "${interface_file}" ]; then
        cat << 'LUNAEOF' > /lunatmp/interface_options.txt

LUNAEOF
        OPTIONS=$(cat /lunatmp/interface_options.txt)
        rm -f /lunatmp/interface_options.*
        # ---------------- INIT ---------------------------
        config_network_init "${interface_name}" "" "${parent_name}" "2" "vlan" "ethernet" "trusted" "${OPTIONS}"
        # ---------------- IPv4 ---------------------------
        if ([ "eth0.1" == "BOOTIF" ] &&  [ "${LUNA_BOOTPROTO}" == "dhcp" ]) || [ "" ]; then
            echo "Using DHCP for IPv4 config"
            config_interface "${interface_name}" "dhcp"
        else
            # -- we call a function below that's being generated by interface plugin
            config_interface "${interface_name}" "10.145.0.104" "16" "255.255.0.0" "" "${parent_name}" "2"  "vlan" "ethernet" "trusted" "${OPTIONS}"
            config_gateway "${interface_name}" "10.145.255.254" "101"
        fi
        config_dns "${interface_name}" "cluster;extern" ""
        # --
        # ---------------- IPv6 ---------------------------
        config_interface_ipv6 "${interface_name}" "linklocal"
      fi
    fi
    
}

function fix_capabilities {
    # tar issue is not fixed yet
    # https://bugzilla.redhat.com/show_bug.cgi?id=771927
    # here is the list of know capabilities:
    # find / -type f -exec getcap '{}' \;
    cat >> "/${rootmnt}/tmp/fix_capabilities.lst" << 'LUNAEOF'
/usr/bin/ping = cap_net_admin,cap_net_raw+p
/usr/bin/ping6 = cap_net_admin,cap_net_raw+p
/usr/bin/gnome-keyring-daemon = cap_ipc_lock+ep
/usr/sbin/arping = cap_net_raw+p
/usr/sbin/clockdiff = cap_net_raw+p
/usr/sbin/fping = cap_net_raw+ep
/usr/sbin/mtr = cap_net_raw+ep
/usr/sbin/fping6 = cap_net_raw+ep
/usr/sbin/suexec = cap_setgid,cap_setuid+ep
LUNAEOF
    chroot "/${rootmnt}" /bin/bash -c " \
        while read FILE d CAP; do \
            /usr/sbin/setcap \${CAP} \${FILE}; \
        done < /tmp/fix_capabilities.lst \
    " > /dev/null 2>&1
    rm -rf "/${rootmnt}/tmp/fix_capabilities.lst"
}

function restore_selinux_context {
    SEPOLICY_FILE=/etc/selinux/targeted/contexts/files/file_contexts
    if [ -f "/${rootmnt}/${SEPOLICY_FILE}" ]; then
        chroot "/${rootmnt}" /bin/bash -c "load_policy -i; setfiles -r / ${SEPOLICY_FILE} /" > "/${rootmnt}/tmp/restore_con.out" 2>&1
    fi
}

function cleanup {
    rm -rf "/${rootmnt}/tmp"/* &> /dev/null
}

echo "Luna2: installer script"
echo "==============================================================================="
echo "Luna2: nodename is node001, member of compute, distribution is redhat 9"
echo "Luna2: install_mode is auto, installing via the lpart pipeline"
echo "==============================================================================="
lunainit
dynamic_ip_check

prescript
write_provisioning_inputs
lpart_phase pre

bmcsetup
hold_for_daemon

# Hardware discovery runs here rather than at the end of the install, and the
# position is the point of it. Everything it reads - dmidecode, /proc, lsblk,
# lspci, /sys/class/net - is available in the installer environment, so nothing
# is gained by waiting for the image: what is lost by waiting is every node whose
# install fails after this point, which reports no hardware at all and is exactly
# the node somebody needs the hardware facts for. Running before partscript also
# means the disks are recorded as found rather than as repartitioned.
update_inventory
partscript
lpart_phase part
[ "$LPART_FALLBACK" = "1" ] && download_image
[ "$LPART_FALLBACK" = "1" ] && unpack_imagefile
collect_mac_n_name_net
change_net
node_secrets
postscript
lpart_phase post



postboot
fix_capabilities
restore_selinux_context
cleanup
update_status "install.success"

# for debugging purposes
#mkdir -p "/${rootmnt}/root/luna2"
#cd /lunatmp
#cat $0 > "/${rootmnt}/root/luna2/installer.sh"
#cp /lunatmp/*.sh "/${rootmnt}/root/luna2/"

