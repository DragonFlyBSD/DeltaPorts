# DragonFly specific port, mainly to allow build chromium...
# Until missing timer_create() und friends prevents to have main sysutils/flock

PORTNAME=	flock
PORTVERSION=	2.19
CATEGORIES=	sysutils
MASTER_SITES=	http://www.zonov.org/${PORTNAME}/ \
		https://leaf.dragonflybsd.org/~zrj/dports/distcache/

PKGNAMESUFFIX+=	-dfly
CONFLICTS_INSTALL+=	flock-[0-9].*

MAINTAINER=	zrj@ef.irc
COMMENT=	Manage locks from shell scripts (dfly compat version)

PLIST_FILES=	bin/${PORTNAME} \
		man/man1/${PORTNAME}.1.gz

do-install:
	${INSTALL_PROGRAM} ${WRKSRC}/${PORTNAME} ${STAGEDIR}${PREFIX}/bin
	${INSTALL_MAN} ${WRKSRC}/${PORTNAME}.1 ${STAGEDIR}${MANPREFIX}/man/man1

.include <bsd.port.mk>
