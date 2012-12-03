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
   M1=${MERGED}/$1
   DP=${DELTA}/ports/$1
   rm -rf ${M1}
   mkdir -p ${M1}

   if [ $2 -eq 1 ]; then
      isDPort=
   else
      isDPort=`grep ^DPORT ${DP}/STATUS`
   fi
   if [ -n "${isDPORT}" ]; then
      cp -r ${DP}/newport/* ${M1}/
   else
      cp  -r ${FPORTS}/$1/* ${M1}/
      if [ -f ${DP}/Makefile.DragonFly ]; then
         cp ${DP}/Makefile.DragonFly ${M1}/
      fi
      if [ -d ${DP}/dragonfly ]; then
         cp -rf ${DP}/dragonfly ${M1}/
      fi
      if [ -d ${DP}/diffs ]; then
         diffs=`find ${DP}/diffs -name \*\.diff`
         for difffile in ${diffs}; do
            patch -d ${M1} < ${difffile}
         done
         rm ${M1}/*.orig
      fi
   fi
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
   elif [ -n "`grep '^(MASK|LOCK)' ${PORT}/STATUS`" ]; then
      # masked or locked, do nothing
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

rm -rf ${MERGED}/Tools ${MERGED}/Templates ${MERGED}/Mk

cp -r ${FPORTS}/Mk        ${MERGED}/
cp -r ${FPORTS}/Tools     ${MERGED}/
cp -r ${FPORTS}/Templates ${MERGED}/

diffs=$(find ${DELTA}/special/Mk/diffs -name \*\.diff)
for difffile in ${diffs}; do
  patch --quiet -d ${MERGED}/Mk < ${difffile}
done
rm ${MERGED}/Mk/*.orig

diffs=$(find ${DELTA}/special/Templates/diffs -name \*\.diff)
for difffile in ${diffs}; do
  patch --quiet -d ${MERGED}/Templates < ${difffile}
done
rm ${MERGED}/Templates/*.orig
