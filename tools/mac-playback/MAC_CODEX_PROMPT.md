Diagnose LANStream Enhanced playback error 4 on a physical iPhone, using this
Mac and the supplied private video controls. Work through the diagnosis and a
minimal verified source fix if evidence supports one. Do not deploy, commit,
push, upload videos, or access private server logs without my authorization.

Read tools/mac-playback/README.md and applicable repository instructions first.
Record the source revision (`git rev-parse HEAD` for a clone, or the handoff
ZIP's SOURCE-IDENTITY.json for a source snapshot), macOS/Safari versions,
iPhone/iOS version, and the
exact server build I tested. If the installed server revision is unknown, say
so; do not assume this source was installed. The current checkout already has
native HLS delivery for Safari. Existing Windows-only checks are not acceptance
on Apple hardware, and the incorrect codec header is an unproven root cause.

1. Inventory available tools without installing anything globally. Run the
   diagnostic script tests. Locate tools/mac-playback/media/. If missing, ask me
   to privately copy that folder from the handoff ZIP. Never search unrelated
   folders for videos or credentials.
2. Run serve_tests.py on loopback. Test the comparison page in actual macOS
   Safari: H.264 fast-start, HEVC hvc1 fast-start, fragmented H.264 with byte ranges,
   fragmented H.264 chunked with generic versus legacy codec headers, and
   fragmented HEVC chunked. The original MKV is an optional negative control.
   Click Play; do not depend on autoplay. Record visible moving picture, sound,
   currentTime, videoWidth/videoHeight, readyState, and error.code/error.message.
   A resolved play promise or advancing time with zero dimensions is not video
   success. Download results locally; redact before sharing.
3. Run the real app using run_lanstream.py with isolated software encoding and
   only the sample folder. Use a local venv and requirements.txt if needed,
   respecting the Mac's dependency-install permissions. Test Normal, Low
   Bandwidth, and optional H.265. Capture currentSrc, selected encoder, and the
   relevant response status, Content-Type, Range/Content-Range, and body type.
   For HLS inspect playlist, init.mp4 and .m4s requests, timestamps, authentication,
   and errors. Confirm an error response isn't an HTML login/error page.
4. With my participation, repeat the same cases on the physical iPhone over
   the private LAN. Bind to the Mac LAN IP only when ready; stop afterward.
   Use Safari Web Inspector following the official guides linked in the README.
   If Safari/device automation isn't available, give precise manual steps and
   wait for actual observations; do not substitute Chromium or Playwright WebKit
   and call that Safari validation. Ask me to log in locally if needed; never
   request passwords or export cookies.
5. Compare the first failing layer across controls: codec/container, delivery,
   actual LANStream encoder output, HLS, or device/network. Separate observations
   from hypotheses. Preserve originals and make the smallest source change
   justified by a reproducible failure. Inspect affected callers and add focused
   regression coverage. Avoid adding more codec modes or replacing the HLS
   implementation without evidence.
6. Return a short report using RESULTS-TEMPLATE.md, exact reproduction steps,
   any patch, and tests actually run. Clearly separate Mac Safari, physical
   iPhone, source fixtures, and installed Windows server results. Mac software
   success does not verify Windows QSV. List any remaining manual acceptance.

The original video and four derived controls are private test inputs. Do not
alter them or include them, private configuration, logs, or results in Git.
