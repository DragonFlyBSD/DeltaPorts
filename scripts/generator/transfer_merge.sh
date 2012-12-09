#!/bin/sh
#
# Copy from merge area to DPorts and commit
#
# $1 <category>/<portname>
# $2 portversions,portrevision

CONFFILE=/usr/local/etc/dports.conf
BUSYFILE=/tmp/merge.busy

if [ -f ${BUSYFILE} ]; then
   ESTABLISHED=$(cat ${BUSYFILE})
   EXPIRED=0
   while [ ${EXPIRED} -eq 0 ]; do
     TIMENOW=$(date "+%s")
     TIMEDIFF=$(expr ${TIMENOW} - ${ESTABLISHED})
     if [ ${TIMEDIFF} -gt 299 ]; then
        EXPIRED=1
     else
        sleep 3
        [ ! -f ${BUSYFILE} ] && EXPIRED=1
     fi
   done
fi

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"

   exit 1
fi

checkdir ()
{
   eval "MYDIR=\$$1"
   if [ ! -d ${MYDIR} ]; then
     echo "The $1 directory (${MYDIR}) does not exist."
     exit 1
  fi
}

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

checkdir DPORTS
checkdir MERGED

date "+%s" > ${BUSYFILE}

oldloc=${MERGED}/${1}
newloc=${DPORTS}/${1}
newdir=$(dirname ${newloc})

if [ -d ${newloc} ]; then
  action="Update"
  reflex="to version ${2}"
  cd ${DPORTS} && git rm -qr ${1}
  rm -rf ${newloc}
else
  action="Import"
  reflex="version ${2}"
fi

mkdir -p ${newdir}
cp -r ${oldloc} ${newloc}

cd ${DPORTS}
git add ${1}
commitmsg="${action} ${1} ${reflex}"
TASKS=$(git status -s --untracked-files=no ${1})
if [ -n "${TASKS}" ]; then
   git commit -q -m "${commitmsg}" --author='Automaton <nobody@home.ok>' ${1}
fi

rm ${BUSYFILE}
