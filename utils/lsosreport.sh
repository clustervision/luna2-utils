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
	for logfile in messages luna pacemaker trinity.log sssd slurm httpd nginx ondemand-nginx my.cnf my.cnf.d; do
		echo "== logfiles $logfile =="
		cp -arL /var/log/$logfile $WORK/log/ 2>&1
	done
	echo
}

function fetch_conf() {
	for conffile in drbd drbd.d sssd slurm sudoers sudoers.d ssh dhcp named http nginx; do
		echo "== conffiles $conffile =="
		cp -arL /etc/$logfile $WORK/etc/ 2>&1
	done
	echo
}

function fetch_var() {
	for varfile in named; do
		echo "== varfiles $varfile =="
		cp -arL /var/$varfile $WORK/var/ 2>&1
	done
	echo
}

function fetch_trix() {
	echo "== trix local files =="
	cp -arL /trinity/local/etc $WORK/trix/ 2>&1
	cp -arL /trinity/local/luna/daemon $WORK/trix/ 2>&1
	cp -arL /trinity/local/luna/cli $WORK/trix/ 2>&1
	echo
}

function fetch_dmesg() {
	dmesg > $WORK/log/dmesg
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

function fetch_ipmi() {
	echo "== ipmi user list =="
	ipmitool user list 1 2>&1
	echo
	echo "== ipmi lan =="
	ipmitool lan print 2>&1
	echo
	ipmitool sel list 2>&1 > others/ipmi-sel-list.dat
}

function fetch_date() {
	echo -n "== date: "
	date
}

function fetch_lunaha() {
	echo "== lmaster =="
	lmaster -a 2>&1
	echo
}

function fetch_pcs() {
	echo "== pcs =="
	pcs status 2>&1
	echo
}

function fetch_drbd() {
	echo "== drbd =="
	drbdadm status 2>&1
	echo
}

function fetch_netstat() {
	echo "== netstat =="
	netstat -tulpen
	echo
	echo "== ss ltnp =="
	ss -ltnp 2>&1
	echo
	echo "== ss lunp =="
	ss -lunp 2>&1
	echo
}

function fetch_trixdiag() {
	echo "== trix diag =="
	trix-diag 2>&1
	echo
}

function fetch_packages() {
	rpm -qa --last > others/package-list.dat
}

function fetch_mounts() {
	echo "== fstab =="
	cat /etc/fstab
	echo
	echo "== mount =="
	mount
	echo
	echo "== proc/mounts =="
	cat /proc/mounts
	echo
}

function fetch_systemctl() {
	systemctl list-units > others/systemctl-units.dat
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
for subdir in log etc var trix others; do
	if [ ! -d $WORK/$subdir ]; then
		mkdir -p $WORK/$subdir
	fi
	ls $subdir &> /dev/null || (echo cannot find $subdir directory $WORK/$subdir and have to bail out; exit 1)
done

(
fetch_date > lsosreport.log
fetch_trixrelease
fetch_projectinfo
fetch_osinfo
fetch_processes
fetch_networkinfo
fetch_firewallinfo
fetch_netstat
fetch_ipmi
fetch_logs
fetch_conf
fetch_var
fetch_trix
fetch_dmesg
fetch_packages
fetch_mounts
fetch_systemctl
fetch_clusterinfo
fetch_trixdiag
fetch_lunaha
fetch_pcs
fetch_drbd
) > lsosreport.log

tar -zcvf $FILE * || (echo "encountered a problem creating $FILE and i have to bail out"; exit 1)
echo
add_message "File $FILE has been created in $WORK. Please send this to ClusterVision"
show_message


