PORTNAME=      decibels
DISTVERSION=   49.6.1
CATEGORIES=    audio gnome
MASTER_SITES=  GNOME
DIST_SUBDIR=   gnome

MAINTAINER=    gnome@FreeBSD.org
COMMENT=       Audio player for GNOME
WWW=           https://apps.gnome.org/Decibels/

LICENSE=       GPLv3+
LICENSE_FILE=  ${WRKSRC}/LICENSE

BUILD_DEPENDS= blueprint-compiler>0:devel/blueprint-compiler \
               tsc:lang/typescript
LIB_DEPENDS=   libadwaita-1.so:x11-toolkits/libadwaita \
               libgjs.so:lang/gjs \
               libgstreamer-1.0.so:multimedia/gstreamer1
RUN_DEPENDS=   gjs-console:lang/gjs

USES=          desktop-file-utils gettext gnome gstreamer localbase \
               meson pkgconfig tar:xz
USE_GNOME=     gtk40 introspection libadwaita
USE_GSTREAMER= core plugins bad good

.include <bsd.port.mk>
