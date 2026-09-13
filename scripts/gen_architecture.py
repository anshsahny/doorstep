# ruff: noqa: E501 - long HTML labels and XML style strings
"""Generate docs/architecture.drawio (then exported to PNG/SVG with the draw.io CLI)."""

from html import escape

cells: list[str] = []
n = 0
INK = "#232F3E"
GREY = "#545B64"


def nid() -> str:
    global n
    n += 1
    return f"n{n}"


def box(x, y, w, h, html, style=""):
    i = nid()
    base = (
        "rounded=1;arcSize=6;whiteSpace=wrap;html=1;fontFamily=Helvetica;fontColor=#232F3E;"
        "fontSize=15;strokeWidth=1.5;"
    )
    cells.append(
        f'<mxCell id="{i}" value="{escape(html)}" style="{base}{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return i


def text(x, y, w, h, html, style=""):
    return box(x, y, w, h, html, "text;strokeColor=none;fillColor=none;" + style)


def icon(cx, y, res, color, label, size=56, plain=False, lw=190):
    i = nid()
    if plain:
        shape = f"shape=mxgraph.aws4.{res};fillColor={INK};strokeColor=none;"
    else:
        shape = f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res};fillColor={color};strokeColor=#ffffff;"
    cells.append(
        f'<mxCell id="{i}" value="" style="{shape}aspect=fixed;html=1;" vertex="1" parent="1">'
        f'<mxGeometry x="{cx - size / 2}" y="{y}" width="{size}" height="{size}" as="geometry"/></mxCell>'
    )
    text(cx - lw / 2, y + size + 2, lw, 44, label, "verticalAlign=top;")
    return i


def sicon(cx, y, res, color, label, size=56, plain=False, lw=180):
    i = icon(cx, y, res, color, "", size=size, plain=plain, lw=10)
    text(cx + size / 2 + 8, y + size / 2 - 24, lw, 48, label, "align=left;verticalAlign=middle;")
    return i


def edge(src, dst, label="", pts=(), style="", exit=None, entry=None):
    i = nid()
    s = (
        f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeWidth=2.5;strokeColor={GREY};"
        "endArrow=block;endFill=1;endSize=7;fontSize=14;fontColor=#232F3E;fontFamily=Helvetica;"
        "labelBackgroundColor=#FFFFFF;"
    )
    if exit:
        s += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
    if entry:
        s += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    p = "".join(f'<mxPoint x="{a}" y="{b}"/>' for a, b in pts)
    arr = f'<Array as="points">{p}</Array>' if pts else ""
    cells.append(
        f'<mxCell id="{i}" value="{escape(label)}" style="{s}{style}" edge="1" parent="1" '
        f'source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry">{arr}</mxGeometry></mxCell>'
    )


def head(x, y, w, t):
    text(x, y, w, 30, f"<b>{t}</b>", "fontSize=18;align=left;")


def b(t, sub=""):
    return f"<b>{t}</b>" + (f"<br><span style='font-size:13px'>{sub}</span>" if sub else "")


# ---- canvas frame -------------------------------------------------------------------------
W, H = 1420, 930
box(0, 0, W, H, "", "fillColor=#FFFFFF;strokeColor=none;rounded=0;")
text(
    20,
    8,
    1100,
    36,
    "<b>Doorstep</b> — heat check-ins built with <b>Strands Agents</b> on <b>Amazon Bedrock AgentCore</b>",
    "fontSize=22;align=left;",
)

GROUP = "fillColor=#F7F9FA;strokeColor=#AAB7B8;dashed=0;verticalAlign=top;"
PLANNED = "dashed=1;dashPattern=6 4;"
Y0, Y1 = 90, 675
SMALL = "fontSize=12;"

# ---- 1. alert sources & ingress -------------------------------------------------------------
box(15, Y0, 270, Y1 - Y0, "", GROUP)
head(95, Y0 + 6, 190, "1 · Alerts &amp; ingress")
apigw = sicon(
    60,
    150,
    "api_gateway",
    "#8C4FFF",
    b("API Gateway", "HTTP API: Telegram webhook,<br>replay, voice link"),
)
lam = sicon(
    60,
    300,
    "lambda",
    "#ED7100",
    b("AWS Lambda", "poller · webhook · replay<br>voice link · caps · kill switch"),
)
sch = sicon(60, 450, "eventbridge_scheduler", "#E7157B", b("EventBridge Scheduler", "every 10 min"))
nws = sicon(60, 580, "alert", INK, b("NWS alerts API", "api.weather.gov"), plain=True)
edge(apigw, lam)
edge(sch, lam)
edge(nws, lam, exit=(0, 0.5), entry=(0, 0.5), pts=[(24, 608), (24, 328)])

# ---- 2. coordinator on AgentCore Runtime ---------------------------------------------------
box(
    310,
    Y0,
    420,
    Y1 - Y0,
    "",
    "fillColor=#EEF8F6;strokeColor=#01A88D;strokeWidth=2;verticalAlign=top;",
)
cells.append(
    f'<mxCell id="{nid()}" value="" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.bedrock_agentcore;'
    f'fillColor=#01A88D;strokeColor=#ffffff;aspect=fixed;html=1;" vertex="1" parent="1">'
    f'<mxGeometry x="322" y="{Y0 + 8}" width="40" height="40" as="geometry"/></mxCell>'
)
text(
    370,
    Y0 + 4,
    350,
    48,
    "<b>2 · Coordinator</b><br><span style='font-size:14px'>Amazon Bedrock AgentCore Runtime</span>",
    "fontSize=18;align=left;",
)
CARD = "fillColor=#FFFFFF;strokeColor=#01A88D;align=left;spacingLeft=10;"
graph = box(
    325,
    150,
    390,
    76,
    b(
        "Strands Graph",
        "Alert assessor → Triage → Outreach<br>profile gate · risk scores · call waves",
    ),
    CARD,
)
clf = box(
    325,
    238,
    390,
    84,
    b(
        "Classifier + deterministic backstop",
        "model proposes → protocol check → mid-call flag<br>→ red-flag phrases: may only raise, never lower",
    ),
    CARD,
)
disp = box(
    325,
    334,
    390,
    76,
    b(
        "Dispatcher · Strands agent loop",
        "tools: escalate, send volunteer, notify family,<br>place_checkin_call …",
    ),
    CARD,
)
intr = box(
    325,
    422,
    390,
    66,
    b("Strands interrupts", "pause for the captain · session snapshot to S3 · resume"),
    CARD,
)
hooks = box(
    325, 500, 390, 56, b("Strands hooks", "ApprovalHook · AuditHook · ModelCallGuard"), CARD
)
bed = icon(360, 572, "bedrock", "#01A88D", "", size=44)
text(
    392,
    568,
    335,
    60,
    b(
        "Amazon Bedrock",
        "Nova 2 Lite (agents) · Nova Micro (simulated<br>residents) · Nova 2 Sonic (voice)",
    ),
    "align=left;",
)
edge(
    lam,
    graph,
    "Invoke-<br>AgentRuntime",
    exit=(1, 0),
    entry=(0, 0.5),
    pts=[(270, 300), (270, 188)],
    style="fontSize=13;",
)

# ---- 3. check-ins over voice --------------------------------------------------------------
box(755, Y0, 250, Y1 - Y0, "", GROUP)
head(770, Y0 + 6, 230, "3 · Check-ins over voice")
res = sicon(
    800, 142, "mobile_client", INK, b("Residents", "browser or phone"), plain=True, size=44, lw=150
)
VOICE = "fillColor=#FFFFFF;align=left;spacingLeft=8;"
bvoice = box(
    770,
    210,
    222,
    92,
    b(
        "AgentCore Runtime · voice",
        "Strands BidiAgent · Nova 2 Sonic<br>browser WebSocket, 60 s<br>single-use presigned link",
    ),
    VOICE + "strokeColor=#01A88D;strokeWidth=2;",
)
dialer = box(
    770,
    330,
    222,
    84,
    b("checkin_worker · Lambda", "SQS job → re-checks live mode,<br>allowlist, subaccount → dials"),
    VOICE + "strokeColor=#ED7100;",
)
twi = box(
    770,
    442,
    222,
    50,
    b("Twilio Voice", "subaccount · Media Streams"),
    VOICE + "strokeColor=#F22F46;",
)
bridge = box(
    770,
    520,
    222,
    84,
    b("Phone bridge", "same voice code · μ-law 8 kHz<br>(local + ngrok for the demo)"),
    VOICE + "strokeColor=#545B64;",
)
edge(
    res,
    bvoice,
    "browser",
    style="startArrow=block;startFill=1;" + SMALL,
    exit=(0.5, 1),
    entry=(0.136, 0),
)
edge(
    res,
    twi,
    "phone",
    style="startArrow=block;startFill=1;" + SMALL,
    exit=(0.5, 0),
    entry=(1, 0.5),
    pts=[(800, 134), (999, 134), (999, 467)],
)
edge(dialer, twi, "Twilio REST", exit=(0.5, 1), entry=(0.5, 0), style=SMALL)
edge(twi, bridge, style="startArrow=block;startFill=1;", exit=(0.5, 1), entry=(0.5, 0))
edge(disp, dialer, "", exit=(1, 0.34), entry=(0, 0.5), style=SMALL)
edge(bvoice, clf, exit=(0, 0.35), entry=(1, 0.25))
edge(
    bridge,
    clf,
    "",
    exit=(0, 0.5),
    entry=(1, 0.75),
    pts=[(747, 562), (747, 301)],
    style=SMALL,
)

text(
    760,
    606,
    240,
    62,
    "<b>place_checkin_call</b> → SQS → dialer<br>both voice paths send the <b>mid-call page</b><br>and the transcript to the coordinator",
    "fontSize=12;align=left;verticalAlign=top;",
)

# ---- 4. Cedar on the tool boundary --------------------------------------------------------
ced = box(1030, Y0, 140, Y1 - Y0, "", "fillColor=#FDECEE;strokeColor=#DD344C;strokeWidth=2;")
cells.append(
    f'<mxCell id="{nid()}" value="" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.policy;'
    f'fillColor=#DD344C;strokeColor=#ffffff;aspect=fixed;html=1;" vertex="1" parent="1">'
    f'<mxGeometry x="1072" y="{Y0 + 14}" width="56" height="56" as="geometry"/></mxCell>'
)
text(
    1035,
    170,
    130,
    480,
    "<b style='font-size:18px'>4 · Cedar</b><br><b>on the tool boundary</b><br><br>"
    "Strands <b>CedarAuthorization</b> checks every dispatcher and outreach tool call<br><br>"
    "call allowlist<br>consent<br>quiet hours<br>volunteer distance<br>sandbox: no real calls<br><br>"
    "allowlist checked again in code by the dialer<br><br>"
    "<i>denials are audited</i>",
    "verticalAlign=top;fontSize=14;",
)
edge(
    hooks,
    ced,
    "every tool call",
    exit=(1, 0.5),
    entry=(0, 0.95),
    pts=[(737, 528), (737, 668), (1012, 668), (1012, 646)],
)

# ---- 5. outputs ---------------------------------------------------------------------------
box(1195, Y0, 210, Y1 - Y0, "", GROUP)
head(1270, Y0 + 6, 130, "5 · Outputs")
cap = sicon(
    1235,
    140,
    "user",
    INK,
    b("Block captain", "Telegram decisions,<br>paged mid-call"),
    plain=True,
    size=48,
    lw=130,
)
vol = sicon(
    1235,
    255,
    "users",
    INK,
    b("Volunteers", "Telegram door-<br>knock tasks"),
    plain=True,
    size=48,
    lw=120,
)
dash = sicon(
    1235,
    380,
    "cloudfront",
    "#8C4FFF",
    b("Dashboard", "React on CloudFront<br><i>planned · Phase 5</i>"),
    size=48,
    lw=130,
)
rep = sicon(
    1235,
    510,
    "documents",
    INK,
    b("Incident report", "cases, decisions, audit<br><i>planned · Phase 5</i>"),
    plain=True,
    size=48,
    lw=130,
)
for t, yy, planned in ((cap, 164, False), (vol, 279, False), (dash, 404, True), (rep, 534, True)):
    edge(ced, t, exit=(1, (yy - Y0) / (Y1 - Y0)), entry=(0, 0.5), style=PLANNED if planned else "")
# the captain's tap comes back through the webhook and resumes the interrupt
edge(
    cap,
    apigw,
    "captain / volunteer taps → webhook → resume interrupt",
    exit=(0.5, 0),
    entry=(0.5, 0),
    pts=[(1235, 66), (60, 66)],
    style="dashed=1;strokeColor=#DD7A00;fontColor=#8A4B00;",
)

# ---- data & operations band ---------------------------------------------------------------
box(15, 695, 1390, 175, "", GROUP)
head(30, 701, 600, "Data, secrets and operations")
band = [
    (
        "dynamodb",
        "#C925D1",
        b("Amazon DynamoDB", "single table: cases,<br>decisions, outbox, claims"),
    ),
    ("s3", "#7AA116", b("Amazon S3", "session snapshots<br>(web assets: Phase 5)")),
    ("sqs", "#E7157B", b("Amazon SQS", "check-in call jobs<br>never retried (DLQ)")),
    (
        "parameter_store",
        "#E7157B",
        b("SSM Parameter Store", "secrets, call allowlist,<br>caps, kill switch"),
    ),
    (
        "bedrock_agentcore",
        "#01A88D",
        b("AgentCore Memory", "resident preferences<br><i>planned · Phase 6</i>"),
    ),
    ("cloudwatch", "#E7157B", b("AgentCore Observability", "CloudWatch + X-Ray traces")),
]
for k, (res_, col, lab) in enumerate(band):
    icon(130 + k * 232, 735, res_, col, lab, size=50, lw=225)

text(
    20,
    878,
    1380,
    44,
    "IaC: AWS CDK (Python) · us-east-1 · Doorstep never calls 911 itself: it tells the resident to call and pages a human · all resident data is fictional · dashed = planned",
    "fontSize=14;fontColor=#545B64;align=left;",
)

xml = (
    '<mxfile host="drawio"><diagram id="doorstep-arch" name="Doorstep architecture">'
    f'<mxGraphModel dx="{W}" dy="{H}" grid="0" gridSize="10" page="1" pageWidth="{W}" pageHeight="{H}" '
    'background="#FFFFFF" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
    + "".join(cells)
    + "</root></mxGraphModel></diagram></mxfile>\n"
)
open(
    __import__("pathlib").Path(__file__).resolve().parents[1] / "docs" / "architecture.drawio", "w"
).write(xml)
