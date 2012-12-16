#!/bin/sh
#
# This is a target of rc.d script
# It wakes up every 30 seconds and commits work
# After 30 wake-ups, it tries to sync with github
#

CONFFILE=/usr/local/etc/dports.conf
LOGFILE=~/gen-dports.log

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   exit 1
fi

checkdir () {
   eval "MYDIR=\$$1"
   if [ ! -d ${MYDIR} ]; then
     echo "The $1 directory (${MYDIR}) does not exist."
     exit 1
  fi
}

split () {
   VAL1=${1}
   VAL2=${2}
   VAL3=${3}
}

confopts=$(grep "=" ${CONFFILE})
for opt in ${confopts}; do
   eval $opt
done

checkdir COMQUEUE
checkdir DPORTS
checkdir DELTA

AWKCMD='{ printf("%s ", $1) }'

rm -f ${LOGFILE}

COUNTER=0
ESTABLISHED=$(date "+%s")
cd ${COMQUEUE}
while [ 1 ]; do
   COUNTER=$(expr ${COUNTER} + 1)
   CANDIDATES1=$(ls -1 delta.* 2>/dev/null)
   CANDIDATES2=$(ls -1 dport.* 2>/dev/null)
   for item in ${CANDIDATES1}; do
     oneline=$(awk "${AWKCMD}" ${item})
     split ${oneline}
     commitmsg="${VAL2}: ${VAL1} v${VAL3}"
     ( cd ${DELTA}/ports && git add ${VAL1}/STATUS )
     if [ $? -eq 0 ]; then
        ( cd ${DELTA}/ports && git commit -q -m "${commitmsg}" ${VAL1}/STATUS )
     fi
     if [ $? -eq 0 ]; then
        rm ${item}
     fi
   done
   for item in ${CANDIDATES2}; do
     oneline=$(awk "${AWKCMD}" ${item})
     split ${oneline}
     if [ "${VAL2}" = "Update" ]; then
       reflex="to version ${VAL3}"
     else
       reflex="version ${VAL3}"
     fi
     commitmsg="${VAL2} ${VAL1} ${reflex}"
     ( cd ${DPORTS} && git add ${VAL1} )
     if [ $? -eq 0 ]; then
        ( cd ${DPORTS} && git commit -q -m "${commitmsg}" ${VAL1} )
     fi
     if [ $? -eq 0 ]; then
        rm ${item}
     fi
   done
   TIMENOW=$(date "+%s")
   TIMEDIFF=$(expr ${TIMENOW} - ${ESTABLISHED})
   if [ ${COUNTER} -eq 30 -o ${TIMEDIFF} -gt 1800 ]; then
      HACK=$(date -j "+Y-%m-%d %H:%M")
      ( cd ${DELTA} && git pull --rebase --quiet 2>/dev/null )
      if [ $? -ne 0 ]; then
         echo "${HACK}:  git pull --rebase on ${DELTA} failed." >> ${LOGFILE}
      else
         HACK=$(date -j "+Y-%m-%d %H:%M")
         ( cd ${DELTA} && git push --quiet )
         if [ $? -ne 0 ]; then
            echo "${HACK}:  git push on ${DELTA} failed." >> ${LOGFILE}
         fi
      fi
      HACK=$(date -j "+Y-%m-%d %H:%M")
      ( cd ${DPORTS} && git push --quiet )
      if [ $? -ne 0 ]; then
         echo "${HACK}:  git push on ${DPORTS} failed." >> ${LOGFILE}
      fi
      COUNTER=0
      ESTABLISHED=$(date "+%s")
   fi
   sleep 30
done
