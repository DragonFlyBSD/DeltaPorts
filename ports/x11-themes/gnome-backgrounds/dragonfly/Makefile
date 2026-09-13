PORTNAME=	gnome-backgrounds
DISTVERSION=	50.0
CATEGORIES=	x11-themes graphics gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	Collection of backgrounds for GNOME
WWW=		https://gitlab.gnome.org/GNOME/gnome-backgrounds

LICENSE=	CC-BY-SA-3.0
LICENSE_FILE=	${WRKSRC}/COPYING

PORTSCOUT=	limit:^47\.

NO_ARCH=	yes
USES=		gettext-runtime meson python:build tar:xz
USE_GNOME=	gdkpixbuf

# DragonFly glycin has an external SVG loader, but the current loader set does
# not include JPEG XL. Convert only the JXL wallpapers to raster at install
# time and leave SVG wallpapers in their original form.
BUILD_DEPENDS=	djxl:graphics/libjxl \
		${PYTHON_PKGNAMEPREFIX}pygobject>0:devel/py-pygobject

post-install:
	@${SH} ${FILESDIR}/dfly-convert-wallpapers.sh \
		${STAGEDIR}${PREFIX}/share/backgrounds/gnome \
		${STAGEDIR}${PREFIX}/share/gnome-background-properties \
		${FILESDIR}/dfly-resize.py

.include <bsd.port.mk>
