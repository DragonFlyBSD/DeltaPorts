#!/bin/sh
#
# Update $MERGED directory (ports + overlay = merged)
#

. /usr/local/etc/dports.conf

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
AWKMOVED='{  FS = "[| ]"; \
if ($1 == "#") \
  print $0; \
else { \
    split($3,a,"-"); \
    if (a[1] > 2012+0) print $0; \
}}'
AWKGID='/nogroup:/ { \
  print "avenger:*:60149:"; \
  print "cbsd:*:60150:"; \
}1'
AWKUID='/nobody:/ { \
  print "avenger:*:60149:60149::0:0:Mail Avenger:/var/spool/avenger:/usr/sbin/nologin"; \
  print "cbsd:*:60150:150::0:0:Cbsd user:/nonexistent:/bin/sh"; \
}1'

TMPFILE=/tmp/tmp.awk
WORKAREA=/tmp/merge.workarea

checkfirst=$(mount | grep ${WORKAREA})
if [ -z "${checkfirst}" ]; then
   rm -rf ${WORKAREA}
   mkdir ${WORKAREA}
   mount -t tmpfs tmpfs ${WORKAREA}
fi

# arg 1 is the original directory
get_legacy ()
{
   local PATT='amd64'
   local RET=$(cd ${1} && grep -lE ${PATT} Makefile* *\.common 2>/dev/null | sort -u)
   echo ${RET}
}

# arg 1 is the worksite directory, replacements are in place
# arg 2+ are legacy array elements
transform ()
{
   local WORK=${1}
   local item
   shift

   while [ $# -gt 0 ]; do
      item=${1}
      shift
      cat ${WORK}/${item} | sed -E \
	 -e 's|OPTIONS_DEFAULT_amd64|OPTIONS_DEFAULT_x86_64|g' \
	 -e 's|OPTIONS_DEFINE_amd64|OPTIONS_DEFINE_x86_64|g' \
	 -e 's|_ON_amd64|_ON_x86_64|g' \
	 -e 's|_OFF_amd64|_OFF_x86_64|g' \
	 -e 's|CFLAGS_amd64|CFLAGS_x86_64|g' \
         -e '/ARCH}.*(amd64|"amd64")/s|amd64|x86_64|g' \
         > ${WORK}/${item}.filtered
      touch -r ${WORK}/${item} ${WORK}/${item}.filtered
      mv ${WORK}/${item}.filtered ${WORK}/${item}
   done
}

fast_and_filtered ()
{
   local ORIG=${1}
   local DEST=${2}
   local LEGACY=$(get_legacy ${ORIG})

   if [ -z "${LEGACY}" ]; then
      cpdup -VV -i0 ${ORIG} ${DEST}
   else
      rm -rf ${WORKAREA}/*
      cp -pr ${ORIG}/* ${WORKAREA}/
      transform ${WORKAREA} ${LEGACY}
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
   local LEGACY

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
            output=$(patch --force --quiet -d ${WORKAREA} -i ${difffile})
            if [ $? -ne 0 -o -n "${output}" ]; then
               echo $1: ${output}
            fi
          done
          frags=$(find ${WORKAREA} -type f -name \*\.orig -print)
          for frag in ${frags}; do
            touch -r ${frag} ${frag%%.orig}
            rm ${frag}
          done
        fi
        LEGACY=$(get_legacy ${WORKAREA})
        transform ${WORKAREA} ${LEGACY}
        cpdup -VV -i0 ${WORKAREA} ${M1}
      fi
   fi
}

split () {
   val_1=${1}
   val_2=${2}
}

awk -F \| "${AWKCMD1}" ${INDEX} > ${TMPFILE}

LINUX=$(cd ${FPORTS} && find * -name Makefile -depth 2 -maxdepth 2 -print | \
    xargs grep -l ^USE_LINUX | sed -e 's|/Makefile||' | sort )

rm -f ${TMPFILE}.linux
for myport in ${LINUX}; do
   case ${myport} in
   net/boinc-client)
      ;;
   *)
      line=$(grep "^${myport} " ${TMPFILE})
      [ -n "${line}" ] && echo ${line} >> ${TMPFILE}.linux
      ;;
   esac
done

echo "searching for custom dports..."
CUSTOM=$(cd ${DELTA}/ports && find * -type f -name STATUS -exec grep -lE '(DPORT|LOCK)' {} \;)
for myport in ${CUSTOM}; do
   fixed=$(echo ${myport}| awk -F \/ "${AWKCMD3}")
   fpc=$(grep "${fixed} " ${TMPFILE})
   [ -z "${fpc}" ] && echo "${fixed} 0" >> ${TMPFILE}
done
echo "done"

sort -u ${TMPFILE} > ${TMPFILE}.sorted

comm -23 ${TMPFILE}.sorted ${TMPFILE}.linux > ${TMPFILE}.final

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
         mkdir -p ${MERGED}/${val_1}
         cpdup -VV -i0 ${DPORTS}/${val_1} ${MERGED}/${val_1}
      elif [ "${ML}" = "MASK" ]; then
         # remove if existed previously
         rm -rf ${MERGED}/${val_1}
      else
         # If previous attempt is same as val_2 then awk sets ML to "SKIP"
         # Here ML is either PORT or DPORT
         merge ${val_1} ${ML}
      fi
   fi
done < ${TMPFILE}.final

rm -f ${TMPFILE}*

rm -rf ${MERGED}/Tools ${MERGED}/Keywords
folders=$(cd ${FPORTS} && find Tools -type d | sort)
for folder in ${folders}; do
   mkdir -p ${MERGED}/${folder}
   touch -r ${FPORTS}/${folder} ${MERGED}/${folder}
done
all=$(cd ${FPORTS}  && find Tools -type f)
for item in ${all}; do
   cat ${FPORTS}/${item} | sed -E \
       -e 's|!/usr/bin/perl|!/usr/local/bin/perl|' \
       > ${MERGED}/${item}
   touch -r ${FPORTS}/${item} ${MERGED}/${item}
done
cp -r ${FPORTS}/Keywords ${MERGED}/

rm -rf ${WORKAREA}/*

cp -a ${FPORTS}/Templates ${WORKAREA}
cp -a ${FPORTS}/Mk ${WORKAREA}
# we don't use bsd.gcc.mk anymore, so remove it to avoid confusion
rm ${WORKAREA}/Mk/bsd.gcc.mk

for k in Mk Templates; do
  diffs=$(find ${DELTA}/special/${k}/diffs -name \*\.diff)
  for difffile in ${diffs}; do
    patch --quiet -d ${WORKAREA}/${k} -i ${difffile} || echo ${difffile}
  done
  frags=$(find ${WORKAREA}/${k} -type f -name \*\.orig -print)
  for frag in ${frags}; do
    touch -r ${frag} ${frag%%.orig}
    rm ${frag}
  done
  cpdup -i0 ${WORKAREA}/${k} ${MERGED}/${k}
done

# port tree root
awk "${AWKGID}" ${FPORTS}/GIDs > ${MERGED}/GIDs
awk "${AWKUID}" ${FPORTS}/UIDs > ${MERGED}/UIDs
diffs=$(find ${DELTA}/special/treetop/diffs -name \*\.diff)
for difffile in ${diffs}; do
  patch --quiet -d ${MERGED} < ${difffile}
done
rm ${MERGED}/*.orig
awk "${AWKMOVED}" ${FPORTS}/MOVED > ${MERGED}/MOVED
touch -r ${FPORTS}/MOVED ${MERGED}/MOVED

umount ${WORKAREA}
rm -rf ${WORKAREA}

LANGS="arabic chinese french german hebrew hungarian japanese korean \
	polish portuguese russian ukrainian vietnamese"

for lang in ${LANGS}; do
  cp -p ${FPORTS}/${lang}/Makefile.inc ${MERGED}/${lang}/Makefile.inc
done

