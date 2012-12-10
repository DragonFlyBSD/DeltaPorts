#!/bin/sh
#
# Create/overwrite STATUS upon build failure
#
# $1 <category>/<portname>
# $2 portversions,portrevision

CONFFILE=/usr/local/etc/dports.conf
BUSYFILE=/tmp/failure.busy

if [ -f ${BUSYFILE} ]; then
   ESTABLISHED=$(cat ${BUSYFILE})
   EXPIRED=0
   while [ ${EXPIRED} -eq 0 ]; do
     TIMENOW=$(date "+%s")
     TIMEDIFF=$(expr ${TIMENOW} - ${ESTABLISHED})
     if [ ${TIMEDIFF} -gt 599 ]; then
        EXPIRED=1
     else
        sleep 3
        [ ! -f ${BUSYFILE} ] && EXPIRED=1
     fi
   done
fi
date "+%s" > ${BUSYFILE}

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   rm -f ${BUSYFILE}
   exit 1
fi

checkdir ()
{
   eval "MYDIR=\$$1"
   if [ ! -d ${MYDIR} ]; then
     echo "The $1 directory (${MYDIR}) does not exist."
     rm -f ${BUSYFILE}
     exit 1
  fi
}

confopts=$(grep "=" ${CONFFILE})
for opt in ${confopts}; do
   eval $opt
done

checkdir DELTA

mkdir -p ${DELTA}/ports/${1}

STATUSFILE=${DELTA}/ports/${1}/STATUS
LOCKFILE=${DELTA}/.git/index.lock

if [ -f ${STATUSFILE} ]; then
   TYPE=`grep PORT ${STATUSFILE}`
   LASTSUCC=`grep "Last success:" ${STATUSFILE} | cut -c 15-80`
   echo ${TYPE} > ${STATUSFILE}
   echo "Last attempt: $2" >> ${STATUSFILE}
   echo "Last success: ${LASTSUCC}" >> ${STATUSFILE}
else
   echo "PORT" > ${STATUSFILE}
   echo "Last attempt: $2" >> ${STATUSFILE}
   echo "Last success: " >> ${STATUSFILE}
fi

if [ -f ${LOCKFILE} ]; then
   EXPIRED=0
   ESTABLISHED=$(date "+%s")
   while [ ${EXPIRED} -eq 0 ]; do
      sleep 3
      TIMENOW=$(date "+%s")
      TIMEDIFF=$(expr ${TIMENOW} - ${ESTABLISHED})
      [ ! -f ${LOCKFILE} ] && EXPIRED=1
      if [ ${TIMEDIFF} -gt 119 ]; then
         exit 1
      fi
   done
fi

cd ${DELTA}/ports
git add ${1}/STATUS
commitmsg="Mark failed build: ${1}

Attempted to build version ${2}"
#TASKS=$(git status -s --untracked-files=no ${1}/STATUS)
#if [ -n "${TASKS}" ]; then
   git commit -q -m "${commitmsg}" --author='Automaton <nobody@home.ok>' ${1}/STATUS
#fi

rm ${BUSYFILE}
