# Created by: John Marino <marino@FreeBSD.org>
# $FreeBSD$

PORTNAME=	synth
PORTVERSION=	0.90
CATEGORIES=	ports-mgmt

MAINTAINER=	marino@FreeBSD.org
COMMENT=	Custom package repository builder for FreeBSD and DragonFly

LICENSE=	ISCL
LICENSE_FILE=	${WRKSRC}/License.txt

IGNORE=		port is work in progress
#BUILD_DEPENDS=	${LOCALBASE}/lib/gnat/util.gpr:${PORTSDIR}/devel/ada-util \
#		${LOCALBASE}/lib/gnat/${IFM}.gpr:${PORTSDIR}/misc/${IFM} \
#		${LOCALBASE}/lib/gnat/${AC}.gpr:${PORTSDIR}/devel/${AC}

USES=		ada:6
USE_GITHUB=	yes
GH_ACCOUNT=	jrmarino
GH_PROJECT=	synth_external:bundle
GH_TAGNAME=	v1.1:bundle 0b02f28

# When framework is moved to Ada6, the ada-util and ini-file-manager
# libraries can be used as prebuilt (switch default.gpr url)

.include <bsd.port.pre.mk>

.if ${OPSYS} == FreeBSD && ${OSREL:R} == 11
USES+=	ncurses:port	# see rant in devel/adacurses/Makefile
.else
USES+=	ncurses
.endif

post-extract:
	@${MV} ${WRKSRC_bundle}/external ${WRKSRC}
	@${MKDIR} ${WRKSRC}/external/lib/util.static \
		${WRKSRC}/external/lib/ini_file_manager \
		${WRKSRC}/external/lib/adacurses \
		${WRKSRC}/build
	@${REINPLACE_CMD} -e "s|/usr/local|${LOCALBASE}|" \
		${WRKSRC}/src/definitions.ads
.if ${OPSYS} == FreeBSD && ${OSREL:R} == 11
	@${REINPLACE_CMD} -e "s|--F11 ||" \
		${WRKSRC}/external/lib/gnat/adacurses.gpr
.endif

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

.include <bsd.port.post.mk>
