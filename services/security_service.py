"""
Security service: password-protect / encrypt PDFs.

Pure Python (no Flask). Prefers ``pikepdf`` (qpdf bindings) for strong AES-256
encryption with a granular permission map; falls back to PyMuPDF's own AES-256
encryption when pikepdf is unavailable.
"""
from __future__ import annotations

import fitz  # PyMuPDF

from utils.file_utils import output_path, with_suffix

# pikepdf is preferred but optional.
try:
    import pikepdf  # type: ignore
    _PIKEPDF_OK = True
except Exception:  # pragma: no cover
    pikepdf = None  # type: ignore
    _PIKEPDF_OK = False


_PERM_DEFAULTS = {"print": True, "modify": True, "copy": True, "annotate": True}


def _resolve_permissions(permissions: dict | None) -> dict:
    """Merge caller permissions over the all-allowed defaults (bools)."""
    perms = dict(_PERM_DEFAULTS)
    if permissions:
        for key in _PERM_DEFAULTS:
            if key in permissions:
                perms[key] = bool(permissions[key])
    return perms


def _protect_pikepdf(path: str, dest: str, user_pw: str, owner_pw: str,
                     perms: dict) -> str:
    """Encrypt with pikepdf using AES-256 (R=6) and a granular permission map."""
    permission = pikepdf.Permission(
        extract=perms["copy"],
        modify_annotation=perms["annotate"],
        modify_assembly=perms["modify"],
        modify_form=perms["modify"],
        modify_other=perms["modify"],
        print_lowres=perms["print"],
        print_highres=perms["print"],
    )
    encryption = pikepdf.Encryption(
        owner=owner_pw, user=user_pw, R=6, allow=permission,
    )
    with pikepdf.open(path) as pdf:
        pdf.save(dest, encryption=encryption)
    return dest


def _protect_fitz(path: str, dest: str, user_pw: str, owner_pw: str,
                  perms: dict) -> str:
    """Fallback encryption with PyMuPDF (AES-256) and a permission bitmask."""
    perm = int(fitz.PDF_PERM_ACCESSIBILITY)  # always allow screen-reader access
    if perms["print"]:
        perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
    if perms["modify"]:
        perm |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ASSEMBLE | fitz.PDF_PERM_FORM
    if perms["copy"]:
        perm |= fitz.PDF_PERM_COPY
    if perms["annotate"]:
        perm |= fitz.PDF_PERM_ANNOTATE
    with fitz.open(path) as doc:
        doc.save(
            dest,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw=owner_pw,
            user_pw=user_pw,
            permissions=perm,
            deflate=True,
            garbage=4,
        )
    return dest


def protect(path: str, job_id: str, *, user_pw: str = "", owner_pw: str = "",
            permissions: dict | None = None) -> list[str]:
    """Encrypt a PDF with AES-256 and the given permission map.

    Parameters
    ----------
    user_pw   : password required to open the document.
    owner_pw  : password granting full control; defaults to *user_pw* if blank.
    permissions : optional ``{"print","modify","copy","annotate"}`` bools
                  (all default ``True``). Disabled perms are denied to readers
                  who only have the user password.

    At least one of *user_pw* / *owner_pw* must be supplied, else
    :class:`ValueError`.
    """
    user_pw = user_pw or ""
    owner_pw = owner_pw or ""
    if not user_pw and not owner_pw:
        raise ValueError("A user or owner password is required to protect the PDF.")
    if not owner_pw:
        owner_pw = user_pw

    perms = _resolve_permissions(permissions)
    dest = output_path(job_id, with_suffix(path, "_protected"))

    if _PIKEPDF_OK:
        try:
            return [_protect_pikepdf(path, dest, user_pw, owner_pw, perms)]
        except Exception:
            # Fall through to the PyMuPDF path on any pikepdf failure.
            pass
    return [_protect_fitz(path, dest, user_pw, owner_pw, perms)]
