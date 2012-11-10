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

AWKCMD='{ n=split($1,a,"-") }{ print substr($2,12) " " a[n] }'
TMPFILE=/tmp/tmp.awk

merge()
{
   echo "merge path $2 : $1"
}


awk -F \| "${AWKCMD}" ${INDEX} > ${TMPFILE}
while read fileline; do
   counter=0
   for element in ${fileline}; do
      counter=$(expr ${counter} '+' 1)
      eval val_${counter}=${element}
   done

   # val_1 = category/portname
   # val_2 = version,portrevision
   PORT=${DELTA}/ports/${val_1}
   
   if [ ! -f ${PORT}/STATUS ]; then
      merge ${val_1} 1
   elif [ -n "`grep ^MASK ${PORT}/STATUS`" ]; then
      # masked, do nothing
   elif [ ! -d ${MERGED}/${val_1} ]; then
      merge ${val_1} 2
   else
      # check previous attempts
      lastatt=`grep "^Last attempt: " ${PORT}/STATUS | cut -c 15-80`
      if [ "${lastatt}" != "${val_2}" ]; then
         merge ${val_1} 3
      fi
   fi
   
done < ${TMPFILE}

rm ${TMPFILE}
