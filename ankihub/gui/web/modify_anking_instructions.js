function ankihubModifyNoteText() {
    for (const textElement of document.querySelectorAll("#text, .editcloze")) {
        textElement.innerHTML = `The AnKing Step Deck <b>starts</b> with all cards suspended <b>except this one</b>.<br><br>
To make more cards available for study:<br>
<a href='#' onclick='pycmd("{{ STEP_TOUR_OPEN_PYCMD }}")'>take the tour on how to unsuspend cards<a/>`;
    }
}

ankihubModifyNoteText();
