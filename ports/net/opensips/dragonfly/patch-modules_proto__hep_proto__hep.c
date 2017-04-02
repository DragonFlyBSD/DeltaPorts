--- modules/proto_hep/proto_hep.c.orig	2017-02-23 11:15:50 UTC
+++ modules/proto_hep/proto_hep.c
@@ -1381,7 +1381,9 @@ static void update_recv_info(struct rece
 	else if(proto == IPPROTO_TCP) ri->proto=PROTO_TCP;
 	else if(proto == IPPROTO_IDP) ri->proto=PROTO_TLS;
 											/* fake protocol */
+#ifndef __DragonFly__
 	else if(proto == IPPROTO_SCTP) ri->proto=PROTO_SCTP;
+#endif
 	else if(proto == IPPROTO_ESP) ri->proto=PROTO_WS;
                                             /* fake protocol */
 	else {
