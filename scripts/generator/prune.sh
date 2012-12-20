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
   allcases=$2
   if [ ! -d ${FPORTS}/${port} -o ${allcases} -eq 1 ]; then
      if [ -d ${MERGED}/${port} ]; then
         echo "Deleting from merge area: ${port}"
         rm -rf ${MERGED}/${port}
      fi
      if [ -d ${DPORTS}/${port} ]; then
         cd ${DPORTS} && git rm -rf ${port}
         commitmsg="Pruning: ${port} removed"
         echo "Deleting from DeltaPorts: ${port}"
         cd ${DPORTS} && git commit -m "${commitmsg}" ${port}
      fi
   fi
}

EXCLUDE="^(Templates/|Tools/|Mk/)"
portdirs=$(cd ${MERGED}; find -s * -type d -depth 1 | grep -vE ${EXCLUDE})
statusdirs=$(cd ${DELTA}/ports; find * -name STATUS -type f)

for port in ${portdirs}; do
   kill_directory ${port} 0
done

for STATUSFILE in ${statusdirs}; do
  MASK=$(grep -E ^MASK ${DELTA}/ports/${STATUSFILE})
  port=`echo ${STATUSFILE} | sed -e 's|\/STATUS$||'`
  if [ -n "${MASK}" ]; then
     kill_directory ${port} 1
  else
     kill_directory ${port} 0
  fi
done
