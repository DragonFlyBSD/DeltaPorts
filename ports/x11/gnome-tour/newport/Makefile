PORTNAME=	gnome-tour
DISTVERSION=	50.0
CATEGORIES=	x11 gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	Tour of the GNOME desktop
WWW=		https://gitlab.gnome.org/GNOME/gnome-tour

LICENSE=	GPLv3+

USES=		cargo desktop-file-utils gettext gnome meson pkgconfig tar:xz
USE_GNOME=	gtk40 libadwaita

CARGO_BUILD=	no
CARGO_INSTALL=	no
CARGO_TEST=	no
CONFIGURE_ENV+=	${CARGO_ENV}
MAKE_ENV+=	${CARGO_ENV}

.include <bsd.port.mk>
