#!/bin/sh
#
# No arguments
# Produces a list of ports matching everything in dports
# Theoretically, everything should build.  In reality, this highlights
# failures on previously-built ports.

CONFFILE=/usr/local/etc/dports.conf
FINALFILE=/tmp/dports.list

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
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

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

AWKCMD='{ print $1 }'
AWKCMD2='{ print substr($2,12) }'

checkdir DPORTS

EXCLUDE="^(Templates/|Tools/|Mk/)"

cd ${DPORTS}
find * -type d -depth 1 -maxdepth 1 | grep -vE ${EXCLUDE} | sort > ${FINALFILE}
