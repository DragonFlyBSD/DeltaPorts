#!/bin/sh
#
# This is a target of rc.d script
# It wakes up every 30 seconds and commits work
# After 30 wake-ups, it tries to sync with github
#

. /usr/local/etc/dports.conf
LOGFILE=~/gen-dports.log

# In a hacky attempt to make this daemon work after a reboot,
# Wait 2 minutes after it is invoked to run.

sleep 120

split () {
   VAL1=${1}
   VAL2=${2}
   VAL3=${3}
}

AWKCMD='{ printf("%s ", $1) }'
AWKCMD2='/^\+/ {if ($1 != "+++") { \
  counter++; \
  if ($1 == "+PORTREVISION=") pv=1; \
}} END { \
  if ((counter == 1) && pv) \
    print "Bump"; \
  else \
    print "Update"; \
}'

rm -f ${LOGFILE}

COUNTER=0
ESTABLISHED=$(date "+%s")
cd ${COMQUEUE}
while [ 1 ]; do
   COUNTER=$(expr ${COUNTER} + 1)
   CANDIDATES1=$(find . -name delta\.\* 2>/dev/null)
   CANDIDATES2=$(find . -name dport\.\* 2>/dev/null)
   for item in ${CANDIDATES1}; do
     oneline=$(awk "${AWKCMD}" ${item})
     split ${oneline}
     commitmsg="${VAL2}: ${VAL1} v${VAL3}"
     ( cd ${DELTA}/ports && git add --all ${VAL1}/STATUS )
     if [ $? -eq 0 ]; then
        TASKS=$(cd ${DELTA}/ports && git status -s --untracked-files=no ${VAL1}/STATUS)
        if [ -z "${TASKS}" ]; then
           rm ${item}
        else
           ( cd ${DELTA}/ports && git commit -q -m "${commitmsg}" ${VAL1}/STATUS )
           if [ $? -eq 0 ]; then
              rm ${item}
           fi
        fi
     fi
   done
   for item in ${CANDIDATES2}; do
     oneline=$(awk "${AWKCMD}" ${item})
     split ${oneline}
     ( cd ${DPORTS} && git add --all ${VAL1} )
     if [ "${VAL2}" = "Update" ]; then
       reflex="to version ${VAL3}"
       VAL2=$(cd ${DPORTS} && git diff HEAD ${VAL1} | awk "${AWKCMD2}")
     else
       reflex="version ${VAL3}"
     fi
     commitmsg="${VAL2} ${VAL1} ${reflex}"
     if [ $? -eq 0 ]; then
        TASKS=$(cd ${DPORTS} && git status -s --untracked-files=no ${VAL1})
        if [ -z "${TASKS}" ]; then
           rm ${item}
        else
           ( cd ${DPORTS} && git commit -q -m "${commitmsg}" ${VAL1} )
           if [ $? -eq 0 ]; then
              rm ${item}
           fi
        fi
     fi
   done
   TIMENOW=$(date "+%s")
   TIMEDIFF=$(expr ${TIMENOW} - ${ESTABLISHED})
   if [ ${COUNTER} -eq 30 -o ${TIMEDIFF} -gt 1800 ]; then
      HACK=$(date -j "+Y-%m-%d %H:%M")
      ( cd ${DELTA} && git pull --quiet --no-edit 2>/dev/null )
      if [ $? -ne 0 ]; then
         echo "${HACK}:  git pull on ${DELTA} failed." >> ${LOGFILE}
      else
         HACK=$(date -j "+Y-%m-%d %H:%M")
         ( cd ${DELTA} && git push --quiet )
         if [ $? -ne 0 ]; then
            echo "${HACK}:  git push on ${DELTA} failed." >> ${LOGFILE}
         fi
      fi
      HACK=$(date -j "+Y-%m-%d %H:%M")
      ( cd ${DPORTS} && git pull --quiet --no-edit 2>/dev/null )
      if [ $? -ne 0 ]; then
         echo "${HACK}:  git pull on ${DPORTS} failed." >> ${LOGFILE}
      else
         HACK=$(date -j "+Y-%m-%d %H:%M")
         ( cd ${DPORTS} && git push --quiet )
         if [ $? -ne 0 ]; then
            echo "${HACK}:  git push on ${DPORTS} failed." >> ${LOGFILE}
         fi
      fi
      COUNTER=0
      ESTABLISHED=$(date "+%s")
   fi
   sleep 30
done
