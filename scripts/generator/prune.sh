#!/bin/sh

CONFFILE=/usr/local/etc/dports.conf

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   exit 1
fi

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

if [ ! -d ${FPORTS} ]; then
   echo "The FPORTS directory (${FPORTS}) does not exist."
   exit 1 
fi

if [ ! -d ${MERGED} ]; then
   echo "The MERGED directory (${MERGED}) does not exist."
   exit 1
fi

if [ ! -d ${DPORTS} ]; then
   echo "The DPORTS directory (${DPORTS}) does not exist."
   exit 1
fi

if [ ! -d ${DELTA} ]; then
   echo "The DELTA directory (${DELTA}) does not exist."
   exit 1
fi

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

EXCLUDE="^(Templates/|Tools/)"
portdirs=`cd ${MERGED}; find -s * -type d -depth 1 | grep -vE ${EXCLUDE}`
deltdirs=`cd ${DELTA}/ports; find * -name STATUS -exec grep -lv "^MASK" {} \;`

for port in ${portdirs}; do
   kill_directory $port
done

for STATUS in ${deltdirs}; do
   port=`echo ${STATUS} | sed -e 's|\/STATUS$||'`
   kill_directory $port
done
