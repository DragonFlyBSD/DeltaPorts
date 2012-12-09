#!/bin/sh
#
# Cron job to commit changes to DPorts and DeltaPorts
# DPorts should only be committed from one source
# DeltaPorts could have multiple commits so it must be pulled first

CONFFILE=/usr/local/etc/dports.conf
LOGFILE=/var/log/gen-dports
FBUSY=/tmp/failure.busy
SBUSY=/tmp/success.busy
MBUSY=/tmp/merge.busy

# never start as long as busy files are present
# Eject completely after 5 minutes of waiting

CLEAR=0
CHECKPOINT=$(date "+%s")

while [ ${CLEAR} -eq 0 ]; do
   if [ -f ${FBUSY} -o -f ${SBUSY} -o -f ${MBUSY} ]; then
     TIMENOW=$(date "+%s")
     TIMEDIFF=$(expr ${TIMENOW} - ${CHECKPOINT})
     if [ ${TIMEDIFF} -gt 299 ]; then
        HACK=$(date -j "+Y-%m-%d %H:%M")
        echo "${HACK}:  Cron job timed out due to present busy files." >> ${LOGFILE}
        exit 0
     else
        sleep 3
     fi
   else
      CLEAR=1
   fi
done

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

date "+%s" > ${FBUSY}
cp ${FBUSY} ${SBUSY}
cp ${FBUSY} ${MBUSY}

HACK=$(date -j "+Y-%m-%d %H:%M")

cd ${DELTA}
git pull --rebase
if [ $? -ne 0 ]; then
  echo "${HACK}:  git pull --rebase on ${DELTA} failed." >> ${LOGFILE}
  rm -f ${FBUSY}
  rm -f ${SBUSY}
  rm -f ${MBUSY}
  exit 0
fi

git push
if [ $? -ne 0 ]; then
  echo "${HACK}:  git push on ${DELTA} failed." >> ${LOGFILE}
  rm -f ${FBUSY}
  rm -f ${SBUSY}
  rm -f ${MBUSY}
  exit 0
fi

cd ${DPORTS}
git push
if [ $? -ne 0 ]; then
  echo "${HACK}:  git push on ${DPORTS} failed." >> ${LOGFILE}
  rm -f ${FBUSY}
  rm -f ${SBUSY}
  rm -f ${MBUSY}
  exit 0
fi

rm -f ${FBUSY}
rm -f ${SBUSY}
rm -f ${MBUSY}
