#!/bin/sh
#
# Cron job to commit changes to DPorts and DeltaPorts
# DPorts should only be committed from one source
# DeltaPorts could have multiple commits so it must be pulled first

CONFFILE=/usr/local/etc/dports.conf
LOGFILE=/var/log/gen-dports

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
checkdir DPORTS

HACK=$(date -j "+Y-%m-%d %H:%M")

cd ${DELTA}
git --rebase pull
if [ $? -ne 0 ]; then
  echo "${HACK}:  git --rebase pull on ${DELTA} failed." >> ${LOGFILE}
  exit 0
fi

git push
if [ $? -ne 0 ]; then
  echo "${HACK}:  git push on ${DELTA} failed." >> ${LOGFILE}
  exit 0
fi

cd ${DPORTS}
git push
if [ $? -ne 0 ]; then
  echo "${HACK}:  git push on ${DPORTS} failed." >> ${LOGFILE}
  exit 0
fi
