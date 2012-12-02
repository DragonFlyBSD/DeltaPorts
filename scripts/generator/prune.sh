#!/bin/sh

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

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

checkdir DELTA
checkdir DPORTS
checkdir FPORTS
checkdir MERGED

kill_directory ()
{
   port=$1
   if [ ! -d ${FPORTS}/${port} ]; then
      echo "Deleting: $port"
      rm -rf ${MERGED}/$port
      if [ -d ${DPORTS}/$port ]; then
         git rm -rf ${DPORTS}/$port

         commitmsg="\"Pruning: $port removed\""
         TASKS=`cd ${DPORTS}; git status -s --untracked-files=no $port`
         if [ -n "${TASKS}" ]; then
            cd ${DPORTS} && git commit -m ${commitmsg}
         fi
      fi
   fi
}

EXCLUDE="^(Templates/|Tools/|Mk/)"
portdirs=`cd ${MERGED}; find -s * -type d -depth 1 | grep -vE ${EXCLUDE}`
deltdirs=`cd ${DELTA}/ports; find * -name STATUS -exec grep -lv "^MASK" {} \;`

for port in ${portdirs}; do
   kill_directory $port
done

for STATUS in ${deltdirs}; do
   port=`echo ${STATUS} | sed -e 's|\/STATUS$||'`
   kill_directory $port
done
