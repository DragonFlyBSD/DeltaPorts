PORTNAME=	linux-firmware
PORTVERSION=	20260622
CATEGORIES=	sysutils
MASTER_SITES=	KERNEL_ORG/linux/kernel/firmware
DISTNAME=	linux-firmware-${PORTVERSION}
DISTFILES=	${DISTNAME}${EXTRACT_SUFX}

MAINTAINER=	lileding@gmail.com
COMMENT=	${FLAVOR:tu:S/NONFREE/ non-free/} firmware images for Linux drivers
WWW=		https://gitlab.com/kernel-firmware/linux-firmware

LICENSE=	LINUX-FIRMWARE
LICENSE_NAME_LINUX-FIRMWARE=	Assorted vendor firmware licenses, enumerated in WHENCE
LICENSE_FILE_LINUX-FIRMWARE=	${WRKSRC}/WHENCE
LICENSE_PERMS_LINUX-FIRMWARE=	dist-mirror pkg-mirror auto-accept
LICENSE_NAME=	${LICENSE_NAME_LINUX-FIRMWARE}
LICENSE_FILE=	${LICENSE_FILE_LINUX-FIRMWARE}
LICENSE_PERMS=	${LICENSE_PERMS_LINUX-FIRMWARE}

FLAVORS=	free nonfree
FLAVOR?=	free

.if ${FLAVOR} == nonfree
PKGNAMESUFFIX=	-nonfree
PLIST=		${.CURDIR}/pkg-plist.nonfree
OPTIONS_SLAVE=	NONFREE
GH_TUPLE=	intel:Intel-Linux-Processor-Microcode-Data-Files:microcode-20260512:intel
USE_GITHUB=	nodefault
RUN_DEPENDS=	${LOCALBASE}/lib/firmware/av7110/bootcode.bin:sysutils/firmware@free
LICENSE_COMB=	multi
LICENSE+=	INTEL-UCODE
LICENSE_NAME_INTEL-UCODE=	Intel Software License Agreement
LICENSE_FILE_INTEL-UCODE=	${WRKSRC_intel}/license
LICENSE_PERMS_INTEL-UCODE=	dist-mirror no-dist-sell pkg-mirror no-pkg-sell auto-accept
.else
PKGNAMESUFFIX=
PLIST=		${.CURDIR}/pkg-plist.free
.endif

OPTIONS_DEFINE=	NONFREE
NONFREE_DESC=	Install non-free firmware and Intel CPU microcode

USES=		tar:xz

NO_BUILD=	yes
NO_ARCH=	yes

# The upstream script is still used to resolve WHENCE entries and preserve
# Linux-firmware's directory layout.  The selected flavor is copied out of a
# temporary complete tree so one source archive can produce independent free
# and non-free packages.
#
# "make dedup" upstream replaces duplicate images with symlinks.  It is not
# used here: it reclaims 73 MB of 1840 MB, which does not pay for a dependency
# on rdfind and on GNU ln --relative.
do-install:
	@${MKDIR} ${WRKDIR}/firmware/lib/firmware
	@cd ${WRKSRC} && ${SH} ./copy-firmware.sh ${WRKDIR}/firmware/lib/firmware
	@${INSTALL_DATA} ${WRKSRC}/WHENCE ${WRKSRC}/LICENSE \
		${WRKDIR}/firmware/lib/firmware/
	@${MKDIR} ${WRKDIR}/firmware/lib/firmware/LICENSES
	@${INSTALL_DATA} ${WRKSRC}/LICENSES/* \
		${WRKDIR}/firmware/lib/firmware/LICENSES/
.if ${FLAVOR} == nonfree
	@${MKDIR} ${WRKDIR}/firmware/lib/firmware/intel-ucode
	@${INSTALL_DATA} ${WRKSRC_intel}/intel-ucode/* \
		${WRKDIR}/firmware/lib/firmware/intel-ucode/
	@${MKDIR} ${WRKDIR}/firmware/lib/firmware/intel-ucode-with-caveats
	@${INSTALL_DATA} ${WRKSRC_intel}/intel-ucode-with-caveats/* \
		${WRKDIR}/firmware/lib/firmware/intel-ucode-with-caveats/
	@${INSTALL_DATA} ${WRKSRC_intel}/license \
		${WRKDIR}/firmware/lib/firmware/LICENSES/LICENSE.intel-ucode
.endif
	@set -e; while IFS= read -r file; do \
		dir=$${file%/*}; \
		${MKDIR} "${STAGEDIR}${PREFIX}/$${dir}"; \
		src="${WRKDIR}/firmware/$${file}"; \
		dst="${STAGEDIR}${PREFIX}/$${file}"; \
		if [ -L "$${src}" ]; then \
			${LN} -s "$$(readlink "$${src}")" "$${dst}"; \
		else \
			${CP} -P "$${src}" "$${dst}"; \
		fi; \
	done < ${PLIST}

.include <bsd.port.mk>
