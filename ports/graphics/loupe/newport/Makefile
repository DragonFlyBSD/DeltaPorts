PORTNAME=	loupe
DISTVERSION=	50.0
CATEGORIES=	graphics gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome
DISTFILES=	${DISTNAME}${EXTRACT_SUFX}

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	Image viewer for GNOME
WWW=		https://gitlab.gnome.org/GNOME/loupe

LICENSE=	GPLv3+

BUILD_DEPENDS=	blueprint-compiler:devel/blueprint-compiler
LIB_DEPENDS=	libglycin-2.so:graphics/glycin \
		libgweather-4.so:net/libgweather4 \
		liblcms2.so:graphics/lcms2

USES=		cargo desktop-file-utils gettext gnome meson pkgconfig tar:xz
USE_GNOME=	glib20 gtk40 libadwaita

CARGO_BUILD=	no
CARGO_INSTALL=	no
CARGO_TEST=	no
CONFIGURE_ENV+=	${CARGO_ENV}
MAKE_ENV+=	${CARGO_ENV}

MESON_ARGS=	-Dprofile=release \
		-Dx11=disabled

GLIB_SCHEMAS=	org.gnome.Loupe.gschema.xml

.include <bsd.port.mk>
