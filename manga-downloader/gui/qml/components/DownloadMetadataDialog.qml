import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "downloadMetadataDialog"
    title: "Preparar download"
    modal: true
    anchors.centerIn: parent
    width: Math.min(560, parent.width - 32)
    property var originalManga: ({})
    property var chapters: []
    property string scanlator: "Any"
    property string cover: ""
    property string coverPreview: ""
    signal confirmed(var manga, var chapters, string format, string scanlator)

    function prepare(manga, selected, preference, format) {
        originalManga = Object.assign({}, manga)
        chapters = selected.slice()
        scanlator = preference
        titleField.text = manga.title || ""
        authorField.text = manga.author || ""
        restoreCover()
        formatField.currentIndex = Math.max(0, formatField.model.indexOf(format))
        layoutField.currentIndex = SettingsBridge.options.pdf_layout === "webcomic" ? 1 : 0
        open()
    }
    function restoreCover() {
        cover = originalManga.poster_url || ""
        coverPreview = originalManga.poster_source || cover
    }
    function submit() {
        if (!titleField.text.trim() || chapters.length === 0)
            return
        var manga = Object.assign({}, originalManga)
        manga.title = titleField.text.trim()
        manga.author = authorField.text.trim()
        manga.poster_url = cover
        manga.poster_source = coverPreview
        manga.combine_chapters = true
        manga.pdf_layout = layoutField.currentIndex === 1 ? "webcomic" : "pages"
        confirmed(manga, chapters, formatField.currentText, scanlator)
        close()
    }
    contentItem: ColumnLayout {
        spacing: 12
        Label {
            Layout.fillWidth: true
            text: "Revise os dados do site antes de baixar."
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            Image {
                Layout.preferredWidth: 120
                Layout.preferredHeight: 170
                source: root.coverPreview
                asynchronous: true
                fillMode: Image.PreserveAspectFit
            }
            ColumnLayout {
                Layout.fillWidth: true
                Label { text: "Nome do mangá / arquivo" }
                TextField {
                    id: titleField
                    objectName: "bookTitleInput"
                    Layout.fillWidth: true
                    placeholderText: "Nome do mangá"
                    selectByMouse: true
                }
                Label { text: "Autor" }
                TextField {
                    id: authorField
                    objectName: "bookAuthorInput"
                    Layout.fillWidth: true
                    placeholderText: "Autor não informado pelo site"
                    selectByMouse: true
                }
                RowLayout {
                    Button {
                        objectName: "chooseBookCoverButton"
                        text: "Trocar capa"
                        onClicked: {
                            var selected = DownloadBridge.chooseCover()
                            if (selected) {
                                root.cover = selected
                                root.coverPreview = selected
                            }
                        }
                    }
                    Button { text: "Capa do site"; onClicked: root.restoreCover() }
                }
            }
        }
        RowLayout {
            Label { text: "Formato" }
            ComboBox { id: formatField; objectName: "bookFormatSelector"; model: ["pdf", "epub", "cbz", "images"] }
        }
        RowLayout {
            visible: formatField.currentText === "pdf"
            Label { text: "Tipo de leitura" }
            ComboBox {
                id: layoutField
                objectName: "bookLayoutSelector"
                Layout.fillWidth: true
                model: ["Mangá — páginas separadas", "Tira longa — leitura vertical"]
            }
        }
        Label {
            Layout.fillWidth: true
            visible: formatField.currentText === "pdf"
            wrapMode: Text.Wrap
            text: layoutField.currentIndex === 1
                  ? "Une as imagens em tiras verticais por capítulo no PDF."
                  : "Mantém cada imagem em uma página do PDF."
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: ["pdf", "epub"].indexOf(formatField.currentText) >= 0
                  ? "Os capítulos selecionados serão reunidos em um único livro, com capa, título e autor."
                  : "Este formato mantém os arquivos separados por capítulo."
        }
    }
    footer: DialogButtonBox {
        Button {
            objectName: "confirmBookDownloadButton"
            text: "Baixar"
            enabled: titleField.text.trim().length > 0 && root.chapters.length > 0
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            onClicked: root.submit()
        }
        Button {
            text: "Cancelar"
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            onClicked: root.close()
        }
    }
}
