#!/bin/sh

. /usr/local/etc/dports.conf

CATEGORIES=$(cd ${MERGED} && find -s * -type d -depth 0 -maxdepth 0 -not \( -name Mk -o -name Tools -o -name Templates \) )

rm -f ${MERGED}/Makefile
for CAT in ${CATEGORIES}; do
echo "SUBDIR += ${CAT}" >> ${MERGED}/Makefile
done


for CAT in ${CATEGORIES}; do
   rm -f ${MERGED}/${CAT}/Makefile
   PORTS=$(cd ${MERGED}/${CAT} && find -s * -type d -depth 0 -maxdepth 0)
   for port in ${PORTS}; do
      echo "SUBDIR += ${port}" >> ${MERGED}/${CAT}/Makefile
   done
done