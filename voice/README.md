# voice/

`doorstep_voice`: a Strands `BidiAgent` on Nova 2 Sonic running the same check-in protocol as
text. The browser path is deployed as a second AgentCore Runtime (`entrypoint.py`, WebSocket via
a 60 s single-use presigned link). The phone path (`phone.py`, Twilio Media Streams, 8 kHz
mu-law) is the same code run on the operator's machine behind ngrok (`make phone-bridge`); it is
not hosted.
