# hook_bulk_build_start.sh
#
# $1 = "bulk_build_start"
# $2 = <jail used>
# $3 = <port tree used>
# $4 = <total queued>

SET_bulk_build_start=fire_bulk_build_start

fire_bulk_build_start() {
	local BASE=/usr/local/poudriere/data/logs/bulk/${2}/${3}
	mkdir -p ${BASE}
	echo "==== Previously Successful Failures ====" > $BASE/PSF.log
}
