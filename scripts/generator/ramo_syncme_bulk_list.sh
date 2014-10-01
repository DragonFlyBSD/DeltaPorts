#!/bin/sh
#
# Accept an origin as an argument (e.g editors/joe)
# produce a list of ports that depend on that origin so they can build.
# suitable for passing to poudriere's bulk -f function

. /usr/local/etc/dports.conf

SYNCFILE=/tmp/syncme
TMPFILE=/tmp/ramo.list
FINALFILE=/tmp/ramosync.list

AWKCMD='{ print $1 }'
AWKCMD2='{ print substr($2,12) }'

if [ ! -f "${SYNCFILE}" ]; then
   echo "The ${SYNCFILE} file does not exist"
   exit 1
fi

rm -f ${FINALFILE} ${FINALFILE}.tmp


while read line; do
   if [ -n "${line}" ]; then
      echo "=======================================>> processing ${line}"
      echo ${line} >> ${FINALFILE}.tmp
      ${DELTA}/scripts/generator/ramo_bulk_list.sh ${line}
      [ -f ${TMPFILE} ] && cat ${TMPFILE} >> ${FINALFILE}.tmp
      rm -f ${TMPFILE}
   fi
done < ${SYNCFILE}

[ -f ${FINALFILE}.tmp ] && \
   sort -u ${FINALFILE}.tmp > ${FINALFILE} && \
   rm ${FINALFILE}.tmp
