# Created by: John Marino <marino@FreeBSD.org>
# $FreeBSD$

PORTNAME=	synth
PORTVERSION=	0.90
CATEGORIES=	ports-mgmt

MAINTAINER=	marino@FreeBSD.org
COMMENT=	Custom package repository builder for FreeBSD and DragonFly

IGNORE=		port is work in progress

LICENSE=	ISCL
LICENSE_FILE=	${WRKSRC}/License.txt

#BUILD_DEPENDS=	${LOCALBASE}/lib/gnat/util.gpr:${PORTSDIR}/devel/ada-util \
#		${LOCALBASE}/lib/gnat/${IFM}.gpr:${PORTSDIR}/misc/${IFM}

USES=		ada:6
USE_GITHUB=	yes
GH_ACCOUNT=	jrmarino
GH_PROJECT=	synth_external:bundle
GH_TAGNAME=	v1.0:bundle 1e39801

# When framework is moved to Ada6, the ada-util and ini-file-manager
# libraries can be used as prebuilt (switch default.gpr url)

post-extract:
	@${MV} ${WRKSRC_bundle}/external ${WRKSRC}
	@${MKDIR} ${WRKSRC}/external/lib/util.static \
		${WRKSRC}/external/lib/ini_file_manager \
		${WRKSRC}/build

do-build:
	(cd ${WRKSRC} && ${SETENV} ${MAKE_ENV} gnatmake -P synthexec)
	(cd ${WRKSRC} && ${SETENV} ${MAKE_ENV} gnatmake -P default)

do-install:
	${INSTALL_PROGRAM} ${WRKSRC}/build/synth \
		${STAGEDIR}${PREFIX}/bin
	${INSTALL_PROGRAM} ${WRKSRC}/build/synthexec \
		${STAGEDIR}${PREFIX}/libexec
	${MKDIR} ${STAGEDIR}/var/log/synth \
		${STAGEDIR}/var/synth/live_packages

.include <bsd.port.mk>
