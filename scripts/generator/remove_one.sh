#!/bin/sh
#
# Given a <category>/<port> input, removes this from queue
#
# $1 <category>/<port>
#

CONFFILE=/usr/local/etc/dports.conf

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   exit 1
fi

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done


sed "\|:${1}\$|d" ${QUEUE} > ${QUEUE}.tmp
mv ${QUEUE}.tmp ${QUEUE}


