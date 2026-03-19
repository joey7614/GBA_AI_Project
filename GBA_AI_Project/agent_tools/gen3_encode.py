"""
Tool: encode_text
Converts a plain ASCII string into Gen3 Pokemon character encoding (bytes).
The agent calls this before writing any text into the game's EWRAM dialog buffer.

Reference: pokefirered/include/characters.h
"""

import google.generativeai as genai

# ASCII → Gen3 byte lookup table
_TABLE: dict[str, int] = {
    ' ':  0x00,
    '0':  0xA1, '1':  0xA2, '2':  0xA3, '3':  0xA4,
    '4':  0xA5, '5':  0xA6, '6':  0xA7, '7':  0xA8,
    '8':  0xA9, '9':  0xAA,
    '!':  0xAB, '?':  0xAC, '.':  0xAD, '-':  0xAE,
    '…':  0xB0,
    '"':  0xB1,
    ',':  0xB8, '/':  0xBA,
    "'":  0xB4,
    'A':  0xBB, 'B':  0xBC, 'C':  0xBD, 'D':  0xBE,
    'E':  0xBF, 'F':  0xC0, 'G':  0xC1, 'H':  0xC2,
    'I':  0xC3, 'J':  0xC4, 'K':  0xC5, 'L':  0xC6,
    'M':  0xC7, 'N':  0xC8, 'O':  0xC9, 'P':  0xCA,
    'Q':  0xCB, 'R':  0xCC, 'S':  0xCD, 'T':  0xCE,
    'U':  0xCF, 'V':  0xD0, 'W':  0xD1, 'X':  0xD2,
    'Y':  0xD3, 'Z':  0xD4,
    'a':  0xD5, 'b':  0xD6, 'c':  0xD7, 'd':  0xD8,
    'e':  0xD9, 'f':  0xDA, 'g':  0xDB, 'h':  0xDC,
    'i':  0xDD, 'j':  0xDE, 'k':  0xDF, 'l':  0xE0,
    'm':  0xE1, 'n':  0xE2, 'o':  0xE3, 'p':  0xE4,
    'q':  0xE5, 'r':  0xE6, 's':  0xE7, 't':  0xE8,
    'u':  0xE9, 'v':  0xEA, 'w':  0xEB, 'x':  0xEC,
    'y':  0xED, 'z':  0xEE,
    ':':  0xF0,
    '\n': 0xFE,   # newline — next line in same dialog box
    '\f': 0xFB,   # form feed — wait for A press, clear box, next page
}

EOS = 0xFF


def encode_text(text: str, max_len: int = 127) -> list[int]:
    """Encode a plain-text string into Gen3 Pokemon character bytes.

    Use this whenever you want to display a message in the game dialog box.
    Unknown characters are replaced with a space.
    The returned list always ends with 0xFF (end-of-string sentinel).

    Args:
        text: The message to display (ASCII, max ~127 characters).
        max_len: Maximum payload length before the EOS byte.

    Returns:
        List of Gen3-encoded byte values ending with 0xFF.
    """
    out: list[int] = []
    for ch in text:
        if len(out) >= max_len:
            break
        out.append(_TABLE.get(ch, 0x00))
    out.append(EOS)
    return out


# Gemini function declaration for this tool
TOOL_ENCODE_TEXT = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="encode_text",
            description=(
                "Encode a plain ASCII string into Gen3 Pokemon character bytes "
                "so it can be displayed in the game dialog box. "
                "Call this before writing any dialog response into the game."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "text": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="The message text to encode (ASCII, max 127 chars).",
                    ),
                },
                required=["text"],
            ),
        )
    ]
)


# Tool dispatcher — called by the agent loop when Gemini invokes this tool
def handle(args: dict) -> dict:
    text = args.get("text", "")
    encoded = encode_text(text)
    return {"encoded_bytes": encoded, "length": len(encoded)}
