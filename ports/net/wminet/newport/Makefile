# Submitted by ftigeot

PORTNAME=	wminet
PORTVERSION=	1.2
CATEGORIES=	net
MASTER_SITES=	ftp://ftp.enteract.com/users/rneswold/
DISTNAME=	wmnet-${PORTVERSION}

MAINTAINER=	ports@FreeBSD.org
COMMENT=	Dockable network monitor for WindowMaker

USE_XORG=	x11 xext xpm
MAKE_ENV+=	X11BASE=${PREFIX}

PLIST_FILES=	bin/wminet wminet.1.gz

.include <bsd.port.mk>
