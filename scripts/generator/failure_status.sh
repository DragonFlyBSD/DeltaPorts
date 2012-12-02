#!/bin/sh
#
# Create/overwrite STATUS upon build failure
#
# $1 <category>/<portname>
# $2 portversions,portrevision

CONFFILE=/usr/local/etc/dports.conf

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

confopts=$(grep "=" ${CONFFILE})
for opt in ${confopts}; do
   eval $opt
done

checkdir DELTA

mkdir -p ${DELTA}/ports/${1}

STATUSFILE=${DELTA}/ports/${1}/STATUS

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

cd ${DELTA}
git add ${1}/STATUS
commitmsg="\"Mark failed build: $1\""
TASKS=(git status -s --untracked-files=no ${1}/STATUS)
if [ -n "${TASKS}" ]; then
   git commit -m ${commitmsg} ${1}/STATUS
fi
