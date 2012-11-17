#!/bin/sh
#
# Cascade build failures to downstream dependencies
# This is a recursive script; it calls itself.
#
# $1 <portname>-<version>
# $2 level of recursion starting with 0
# $3 INDEX variable from dports.conf
# $4 QUEUE variable from dports.conf
# $5 FAIL_LOG file name
#
# True recursion can't be used to drill down because secondary dependencies
# can also have multiple effects and get duplicated.  Also some tertiery
# dependencies also list "grandparent" dependencies sending a true recursive
# script into an infinite loop.
#
# Therefore the failure log is done in passes and already-listed failures
# are skipped.
#

PORTNAME=$1
LEVEL=$2
INDEX=$3
QUEUE=$4
REALFLOG=$5

NEXTLEVEL=`expr ${LEVEL} + 1`
INDENT1=`expr ${LEVEL} \* 4`
INDENT2=`expr ${INDENT1} + 2`
LOOKAHEAD=/tmp/lookahead.${LEVEL}
BASEFLOG=/tmp/faillog
FLOG=${BASEFLOG}.${LEVEL}
IFSORIG="${IFS}"
LOCALCNT=0

AWKCMD1='{ if ($1 != portname) print $1 ":" substr($2,12); }'
AWKCMD2='{ if ($1 != portname) print $1; }'


rm -f ${LOOKAHEAD}
touch ${LOOKAHEAD}
if [ ${LEVEL} -eq 0 ]; then
  rm -f ${BASEFLOG}.*
  touch ${FLOG}
else
  printf "%${INDENT1}s%s\n" "" "${PORTNAME}" >> ${FLOG}
fi
DEPLIST=`grep ${PORTNAME} ${INDEX} | awk -F \| -v portname=${PORTNAME} "${AWKCMD1}" - | sort`

for line in ${DEPLIST}; do
  LOCALCNT=`expr ${LOCALCNT} + 1`
  
  IFS=:
  PARTNUM=0
  # val_1 is portname-version
  # val_2 is category/port
  for part in ${line}; do
    PARTNUM=`expr ${PARTNUM} + 1`
    eval "val_${PARTNUM}=${part}"
  done 
  IFS="${IFSORIG}"

  printf "%${INDENT2}s%s\n" "" "${val_1}" >> ${FLOG}
  grep ${val_1} ${INDEX} | awk -F \| -v portname=${val_1} "${AWKCMD2}" - >> ${LOOKAHEAD}

  sed "\|:${val_2}\$|d" ${QUEUE} > ${QUEUE}.tmp
  mv ${QUEUE}.tmp ${QUEUE}
done

LOOKSORTED=`sort ${LOOKAHEAD} | uniq`

for child in ${LOOKSORTED}; do
  PREVSEEN=`grep ${child} ${FLOG}`
  if [ -z "${PREVSEEN}" ]; then
    SUBCOUNT=`$0 ${child} ${NEXTLEVEL} "${INDEX}" "${QUEUE}" "${FLOG}"`
    LOCALCNT=`expr ${LOCALCNT} + ${SUBCOUNT}`
  fi
done

rm -f ${LOOKAHEAD}
if [ ${LEVEL} -eq 0 ]; then
  if [ ${LOCALCNT} -eq 0 ]; then
    printf "%${INDENT1}s%s\n" "" "${PORTNAME}" >> ${REALFLOG}
  else
    printf "%${INDENT1}s%s (impacts: %s)\n" "" "${PORTNAME}" "${LOCALCNT}" >> ${REALFLOG}
  fi
  cat ${BASEFLOG}.* >> ${REALFLOG}
  rm -f ${BASEFLOG}.*
else
  echo ${LOCALCNT}
fi
