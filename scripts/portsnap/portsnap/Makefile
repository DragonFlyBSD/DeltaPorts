SCRIPTS=portsnap.sh
MAN=	portsnap.8

afterinstall:
	install -C ${.CURDIR}/portsnap.conf /usr/local/etc/portsnap.conf

BINDIR= /usr/local/sbin

.include <bsd.prog.mk>
