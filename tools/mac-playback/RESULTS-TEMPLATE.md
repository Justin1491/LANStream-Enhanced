# Playback results

Source revision/snapshot:
Installed Windows server revision (or unknown):
macOS / Safari version:
iPhone / iOS version:
Selected encoder:
Network route (Mac loopback / LAN / VPN):

| Case | Mac Safari picture/sound | iPhone picture/sound | Dimensions/time | Error |
| --- | --- | --- | --- | --- |
| H.264 fast-start / ranges | | | | |
| HEVC hvc1 fast-start / ranges | | | | |
| H.264 fragmented / ranges | | | | |
| H.264 fragmented / chunked / generic header | | | | |
| H.264 fragmented / chunked / old codec header | | | | |
| HEVC fragmented / chunked | | | | |
| App Normal | | | | |
| App Low Bandwidth | | | | |

For each failure: currentSrc (redact personal paths), status/Content-Type,
Range/Content-Range/Transfer-Encoding, error code/message, response body type,
and HLS playlist/init/segment results when applicable.

Reproduction steps:

Observed failing layer:

Hypotheses still unproven:

Minimal patch and focused checks:

Mac Safari verified:
Physical iPhone verified:
Installed Windows server/QSV verified:
Remaining acceptance:
