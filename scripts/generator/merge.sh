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

AWKCMD1='{ n=split($1,a,"-") }{ print substr($2,12) " " a[n] }'
AWKCMD2='{ \
if (FNR == 1) { \
  if ($1 == "MASK") { print $1; exit } \
  else if ($1 == "LOCK" && mex == 1) { print "SKIP"; exit } \
  else if ($1 == "LOCK") { print "LOCK"; exit } \
  else if ($1 == "PORT" || $1 == "DPORT") { hold = $1 } \
  else exit 1; \
} \
if (FNR == 2) \
  if (latt == $3 && mex == 1) { print "SKIP" } \
  else { print hold } \
}'
AWKCMD3='{ print $1 "/" $2 }'

TMPFILE=/tmp/tmp.awk
WORKAREA=/tmp/merge.workarea

checkfirst=$(mount | grep ${WORKAREA})
if [ -z "${checkfirst}" ]; then
   rm -rf ${WORKAREA}
   mkdir ${WORKAREA}
   mount -t tmpfs tmpfs ${WORKAREA}
fi

fast_and_filtered ()
{
   ORIG=${1}
   DEST=${2}
   LEGACY=$(cd ${ORIG} && grep -lE ':(U|L)}|ARCH}.*(amd64|"amd64")' Makefile *\.common 2>/dev/null)
   if [ -z "${LEGACY}" ]; then
      cpdup -VV -i0 ${ORIG} ${DEST}
   else
      rm -rf ${WORKAREA}/*
      cp -pr ${ORIG}/* ${WORKAREA}/
      for item in ${LEGACY}; do
         cat ${ORIG}/${item} | sed -E \
            -e 's|:L}|:tl}|g' \
            -e 's|:U}|:tu}|g' \
            -e '/ARCH}.*(amd64|"amd64")/s|amd64|x86_64|g' \
            > ${WORKAREA}/${item}
      done
      cpdup -VV -i0 ${WORKAREA} ${DEST}
   fi
}

merge()
{
   local M1=${MERGED}/$1
   local DP=${DELTA}/ports/$1
   local REMOVE=${DP}/diffs/REMOVE
   local MD=0
   local DDIFF=0
   local DDRAG=0
   rm -rf ${M1}
   mkdir -p ${M1}

   if [ "${2}" = "FAST" ]; then
      fast_and_filtered "${FPORTS}/${1}" "${M1}"
   elif [ "${2}" = "DPORT" ]; then
      cpdup -VV -i0 ${DP}/newport ${M1}
   else
      [ -f ${DP}/Makefile.DragonFly ] && MD=1
      [ -d ${DP}/dragonfly ] && DDRAG=1
      [ -d ${DP}/diffs ] && DDIFF=1
      if [ ${MD} -eq 0 -a ${DDRAG} -eq 0 -a ${DDIFF} -eq 0 ]; then
        fast_and_filtered "${FPORTS}/${1}" "${M1}"
      else
        rm -rf ${WORKAREA}/*
        cpdup -VV -i0 ${FPORTS}/${1}/ ${WORKAREA}/
        [ ${MD} -eq 1 ] && cp -p ${DP}/Makefile.DragonFly ${WORKAREA}/
        [ ${DDRAG} -eq 1 ] && cp -pr ${DP}/dragonfly ${WORKAREA}/
        if [ ${DDIFF} -eq 1 ]; then
          if [ -f ${REMOVE} ]; then
            while read line; do
              rm ${WORKAREA}/${line}
            done < ${REMOVE}
          fi
          diffs=$(find ${DP}/diffs -name \*\.diff)
          for difffile in ${diffs}; do
            patch --quiet -d ${WORKAREA} < ${difffile}
          done
          find ${WORKAREA} -type f -name \*\.orig -exec rm {} \;
        fi
	LEGACY=$(cd ${WORKAREA} && grep -lE ':(U|L)}|ARCH}.*(amd64|"amd64")' Makefile *\.common 2>/dev/null)
        for item in ${LEGACY}; do
          cat ${WORKAREA}/${item} | sed -E \
             -e 's|:L}|:tl}|g' \
             -e 's|:U}|:tu}|g' \
             -e 's|:U:(.*)}|:tu:\1}|g' \
             -e 's|:L:(.*)}|:tl:\1}|g' \
             -e '/ARCH}.*(amd64|"amd64")/s|amd64|x86_64|g' \
             > ${WORKAREA}/${item}.filtered
          rm ${WORKAREA}/${item}
          mv ${WORKAREA}/${item}.filtered ${WORKAREA}/${item}
        done
        cpdup -VV -i0 ${WORKAREA} ${M1}
      fi
   fi
}

split () {
   val_1=${1}
   val_2=${2}
}

awk -F \| "${AWKCMD1}" ${INDEX} | sort > ${TMPFILE}

echo "searching for custom dports..."
CUSTOM=$(cd ${DELTA}/ports && find * -type f -name STATUS -exec grep -l DPORT {} \;)
for myport in ${CUSTOM}; do
   fixed=$(echo ${myport}| awk -F \/ "${AWKCMD3}")
   fpc=$(grep "${fixed} " ${TMPFILE})
   [ -z "${fpc}" ] && echo "${fixed} 0" >> ${TMPFILE}
done
echo "done"

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
         # Entry already exists and 
         #  - it's locked -or-
         #  - last attempt = current version (val_2)
      elif [ "${ML}" = "LOCK" ]; then
         # Locked and merged entry doesn't exist.  Copy from DPorts
         cpdup -VV -i0 ${DPORTS}/${val_1}/ ${MERGED}/${val_1}/
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
cp ${DPORTS}/GIDs ${DPORTS}/UIDs ${MERGED}/

rm -rf ${WORKAREA}/*

cp -a ${FPORTS}/Templates ${WORKAREA}
mkdir -p ${WORKAREA}/Mk/Uses
all=$(cd ${FPORTS} && find Mk -type f)
for item in ${all}; do
   cat ${FPORTS}/${item} | sed -E \
      -e 's|:L}|:tl}|g' \
      -e 's|:U}|:tu}|g' \
      -e 's|:U:(.*)}|:tu:\1}|g' \
      -e 's|:L:(.*)}|:tl:\1}|g' \
      > ${WORKAREA}/${item}
done

for k in Mk Templates; do
  diffs=$(find ${DELTA}/special/${k}/diffs -name \*\.diff)
  for difffile in ${diffs}; do
    patch --quiet -d ${WORKAREA}/${k} < ${difffile}
  done
  find ${WORKAREA}/${k} -name \*\.orig -exec rm {} \;
  cpdup -i0 ${WORKAREA}/${k} ${MERGED}/${k}
done

umount ${WORKAREA}
rm -rf ${WORKAREA}
