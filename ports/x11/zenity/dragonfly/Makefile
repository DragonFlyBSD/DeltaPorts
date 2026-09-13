PORTNAME=	zenity
DISTVERSION=	4.2.2
CATEGORIES=	x11 gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	Display GNOME dialogs from the command line
WWW=		https://gitlab.gnome.org/GNOME/zenity

LICENSE=	LGPL21+

CONFLICTS_INSTALL=	zenity4-4.*

BUILD_DEPENDS=	itstool:textproc/itstool

USES=		gettext gnome localbase meson pkgconfig \
		perl5 tar:xz xorg
USE_CSTD=	c99
USE_GNOME=	cairo gdkpixbuf gtk40 libadwaita
USE_XORG=	x11
MESON_ARGS=	-Dwebkitgtk=false \
		-Dmanpage=false


.include <bsd.port.mk>
