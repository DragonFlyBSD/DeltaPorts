#!/bin/sh
#
# Update $MERGED directory (ports + overlay = merged)
#

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
AWKCMD2='{ \
if (FNR == 1) { \
  if ($1 == "MASK") { print $1; exit } \
  else if ($1 == "LOCK") { print "SKIP"; exit } \
  else if ($1 == "PORT" || $1 == "DPORT") { hold = $1 } \
  else exit 1; \
} \
if (FNR == 2) \
  if (latt == $3 && mex == 1) { print "SKIP" } \
  else { print hold } \
}'

TMPFILE=~/tmp.awk
WORKAREA=~/merge.workarea

rm -rf ${WORKAREA}
mkdir -p ${WORKAREA}
mount -t tmpfs tmpfs ${WORKAREA}

merge()
{
   local M1=${MERGED}/$1
   local DP=${DELTA}/ports/$1
   local MD=0
   local DDIFF=0
   local DDRAG=0
   rm -rf ${M1}
   mkdir -p ${M1}

   if [ "${2}" = "FAST" ]; then
      cpdup -i0 ${FPORTS}/${1} ${M1}
   elif [ "${2}" = "DPORT" ]; then
      cpdup -i0 ${DP}/newport ${M1}
   else
      [ -f ${DP}/Makefile.DragonFly ] && MD=1
      [ -d ${DP}/dragonfly ] && DDRAG=1
      [ -d ${DP}/diffs ] && DDIFF=1
      if [ ${MD} -eq 0 -a ${DDRAG} -eq 0 -a ${DDIFF} -eq 0 ]; then
        cpdup -i0 ${FPORTS}/${1} ${M1}
      else
        rm -rf ${WORKAREA}/*
        cp -pr ${FPORTS}/$1/* ${WORKAREA}/
        [ ${MD} -eq 1 ] && cp -p ${DP}/Makefile.DragonFly ${WORKAREA}/
        [ ${DDRAG} -eq 1 ] && cp -pr ${DP}/dragonfly ${WORKAREA}/
        if [ ${DDIFF} -eq 1 ]; then
          diffs=$(find ${DP}/diffs -name \*\.diff)
          for difffile in ${diffs}; do
            patch --quiet -d ${WORKAREA} < ${difffile}
          done
          find ${WORKAREA}/ -name \*\.orig -exec rm {} \;
        fi
        cpdup -i0 ${WORKAREA} ${M1}
      fi
   fi
}

split () {
   val_1=${1}
   val_2=${2}
}

awk -F \| "${AWKCMD}" ${INDEX} | sort > ${TMPFILE}
while read fileline; do
   split ${fileline}

   # val_1 = category/portname
   # val_2 = version,portrevision
   PORT=${DELTA}/ports/${val_1}
   
   if [ ! -d ${PORT} ]; then
      merge ${val_1} "FAST"
   else
      MEX=0
      [ -d ${MERGED}/${val_1} ] && MEX=1
      ML=$(awk -v latt=${val_2} -v mex=${MEX} "${AWKCMD2}" ${PORT}/STATUS 2>/dev/null)
      if [ -z "${ML}" ]; then
         # Likely no STATUS FILE exists, consider as PORT
         merge ${val_1} "PORT"
      elif [ "${ML}" = "SKIP" ]; then
         # locked, do nothing
      elif [ "${ML}" = "MASK" ]; then
         # remove if existed previously
         rm -rf ${MERGED}/${val_1}
      else
         # If previous attempt is same as val_2 then awk sets ML to "SKIP"
         # Here ML is either PORT or DPORT
         merge ${val_1} ${ML}
      fi
   fi
done < ${TMPFILE}

rm -f ${TMPFILE}

cpdup -i0 ${FPORTS}/Tools ${MERGED}/Tools

rm -rf ${WORKAREA}/*

for k in Mk Templates; do
  cp -pr ${FPORTS}/${k} ${WORKAREA}/
  diffs=$(find ${DELTA}/special/${k}/diffs -name \*\.diff)
  for difffile in ${diffs}; do
    patch --quiet -d ${WORKAREA}/${k} < ${difffile}
  done
  rm ${WORKAREA}/${k}/*.orig
  cpdup -i0 ${WORKAREA}/${k} ${MERGED}/${k}
done

umount ${WORKAREA}
rm -rf ${WORKAREA}
