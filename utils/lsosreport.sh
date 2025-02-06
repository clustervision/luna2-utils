#!/bin/bash


function add_message() {
  echo -e "$1" | fold -w70 -s >> /tmp/mesg.$$.dat
}

function show_message() {
  echo "****************************************************************************"
  echo "*                                                                          *"
  while read -r LINE
  do
    printf '*  %-70s  *\n' "$LINE"
  done < /tmp/mesg.$$.dat
  echo "*                                                                          *"
  echo "****************************************************************************"
  truncate -s0 /tmp/mesg.$$.dat
}

function show_bare_message() {
  echo
  while read -r LINE
  do
    printf ' %-74s\n' "$LINE"
  done < /tmp/mesg.$$.dat
  echo
  truncate -s0 /tmp/mesg.$$.dat
}

function get_confirmation() {
  local DEFAULT=$1
  local MESG=$2
  local CONFIRM=""
  while [ ! "$CONFIRM" ]; do
    case $DEFAULT in
      [Yy]|yes|Yes|YES)
        echo -n "$MESG? (<y>|n): " >&2
        ;;
      *)
        echo -n "$MESG? (y|<n>): " >&2
        ;;
    esac
    read -t 600 CONFIRM
    RET=$?
    if [ "$RET" == "142" ] || [ ! "$CONFIRM" ]; then
      CONFIRM=$DEFAULT
    fi
    case $CONFIRM in
      [Yy]|yes|Yes|YES)
         echo yes
         ;;
      [Nn]|no|No|NO)
         echo no
         ;;
      *)
         CONFIRM=""
         ;;
    esac
  done
}


function fetch_processes() {
	echo "== process info =="
	ps ax -o %cpu,%mem,uid,user,cmd
	echo
}

function fetch_logs() {
	if [ -d /var/log/luna ]; then
		cp -ar /var/log/luna . && \
		mv luna luna-logs
	else
		echo "== no luna logs available"
	fi
	cp /var/log/messages . && \
	mv messages var-log-messages
}

function fetch_dmesg() {
	dmesg > dmesg.out
}

function fetch_clusterinfo() {
	PROJ=$(cat /trinity/site 2> /dev/null || cat /etc/trinityx-site 2> /dev/null || echo 'unknown')
	if [ -f cluster-conf-${PROJ}.dat ]; then
		rm -f cluster-conf-${PROJ}.dat
	fi
	lexport -c -e cluster-conf-${PROJ}.dat 2>&1
}

function fetch_trixrelease() {
	echo -n "== trix release: "
	cat /etc/trinityx-release 2> /dev/null || echo "trix release not found"
}

function fetch_osinfo() {
	echo "== OS info =="
	cat /etc/redhat-release 2> /dev/null
	cat /etc/lsb-release 2> /dev/null
	echo
	echo -n "== uptime: "
	uptime
	echo
}

function fetch_projectinfo() {
	echo -n "== Project: "
	cat /trinity/site 2> /dev/null || cat /etc/trinityx-site 2> /dev/null || echo "project number not found"
}

function fetch_networkinfo() {
	echo "== network info =="
	ifconfig 2> /dev/null && echo
	route -n 2> /dev/null && echo
	ip a
	echo
	ip route
	echo
}

function fetch_firewallinfo() {
	echo "== firewall =="
	firewall-cmd --list-all 2> /dev/null || echo 'no firewalld running'
	echo
	echo "== iptables =="
	iptables -L 2> /dev/null || echo 'no iptables rules defined'
	echo
}	

function fetch_date() {
	echo -n "== date: "
	date
}

# -------------------------- main ---------------------------

add_message "TrinityX sos gathering utility for support purposes"
show_message
add_message "This utility gathers information from your system and packs it into a tarball which can then be sent to ClusterVision to help troubleshooting problems and issues."
add_message "We gather log files, system information and cluster information."
show_bare_message
GO_AHEAD=$(get_confirmation y "Do you want to proceed")

if [ "$GO_AHEAD" == "no" ]; then
	echo Exiting...
	exit
fi

export PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:$PATH
DATE=$(date +%s)
PROJ=$(cat /trinity/site 2> /dev/null || cat /etc/trinityx-site 2> /dev/null || echo 'unknown')
WORK=/tmp/lsosreport
FILE=lsosreport-${PROJ}-${DATE}.tgz

TMP=$(df /tmp 2> /dev/null|tail -n1|awk '{ print $4 }'|grep -E '[0-9]+'||echo 0)
if [ ! "$TMP" -gt "1000000" ]; then
	ALTTMP=$(df /trinity/tmp 2> /dev/null|tail -n1|awk '{ print $4 }'|grep -E '[0-9]+'||echo 0)
	if [ "$ALTTMP" -gt "1000000" ]; then
		WORK=/trinity/tmp/lsosreport
	fi
fi

if [ ! "$WORK" ]; then
	echo i cannot figure out my temporary working directory and i have to bail out
	exit 1
elif [ "${#WORK}" -lt "3" ]; then
	echo my temporary working directory $WORK looks off and i have to bail out
	exit 1
fi

if [ -d $WORK ]; then
	rm -rf $WORK
fi
if [ ! -d $WORK ]; then
	mkdir -p $WORK
fi
cd $WORK || (echo cannot change into directory $WORK and have to bail out; exit 1)

fetch_date > lsosreport.log
fetch_trixrelease >> lsosreport.log
fetch_projectinfo >> lsosreport.log
fetch_osinfo >> lsosreport.log
fetch_processes >> lsosreport.log
fetch_networkinfo >> lsosreport.log
fetch_firewallinfo >> lsosreport.log
fetch_logs >> lsosreport.log
fetch_dmesg >> lsosreport.log
fetch_clusterinfo >> lsosreport.log

tar -zcvf $FILE * || (echo "encountered a problem creating $FILE and i have to bail out"; exit 1)
echo
add_message "File $FILE has been created in $WORK. Please send this to ClusterVision"
show_message


