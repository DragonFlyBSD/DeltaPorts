#!/bin/sh
#
# Update $MERGED directory (ports + overlay = merged)
#

. /usr/local/etc/dports.conf

WORKAREA=/tmp/merge.workarea
AWKMOVED='{  FS = "[| ]"; \
if ($1 == "#") \
  print $0; \
else { \
    split($3,a,"-"); \
    if (a[1] > 2012+0) print $0; \
}}'

checkfirst=$(mount | grep ${WORKAREA})
if [ -z "${checkfirst}" ]; then
   rm -rf ${WORKAREA}
   mkdir ${WORKAREA}
   mount -t tmpfs tmpfs ${WORKAREA}
fi

rm -rf ${WORKAREA}/*

cp -a ${FPORTS}/Templates ${WORKAREA}
mkdir -p ${WORKAREA}/Mk/Uses ${WORKAREA}/Mk/Scripts
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
    echo "Apply patch ${difffile}" 
    patch --quiet -d ${WORKAREA}/${k} -i ${difffile} || echo ${difffile}
  done
  find ${WORKAREA}/${k} -name \*\.orig -exec rm {} \;
  cpdup -i0 ${WORKAREA}/${k} ${MERGED}/${k}
done

# port tree root
cp ${FPORTS}/UIDs ${FPORTS}/GIDs ${MERGED}/
diffs=$(find ${DELTA}/special/treetop/diffs -name \*\.diff)
for difffile in ${diffs}; do
  patch --quiet -d ${MERGED} < ${difffile}
done
rm ${MERGED}/*.orig
awk "${AWKMOVED}" ${FPORTS}/MOVED > ${MERGED}/MOVED

umount ${WORKAREA}
rm -rf ${WORKAREA}

rm -rf ${MERGED}/Tools ${MERGED}/Keywords
folders=$(cd ${FPORTS} && find Tools -type d | sort)
for folder in ${folders}; do
   mkdir -p ${MERGED}/${folder}
done
all=$(cd ${FPORTS}  && find Tools -type f)
for item in ${all}; do
   cat ${FPORTS}/${item} | sed -E \
       -e 's|!/usr/bin/perl|!/usr/local/bin/perl|' \
       > ${MERGED}/${item}
done
cp -r ${FPORTS}/Keywords ${MERGED}/
