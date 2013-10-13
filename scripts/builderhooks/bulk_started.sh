# Clear PREVIOUSLY SUCCESS FAILURES log at the start of a bulk build

LOGBASE=${POUDRIERE_DATA}/logs/bulk/${MASTERNAME}/latest/logs
[ -d ${LOGBASE} ] && echo "==== Previously Successful Failures ====" > ${LOGBASE}/PSF.log
