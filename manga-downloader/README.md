# Manga Downloader

Downloader para **Comix e MangaDex**, com a interface escura e dourada do
comix-downloader, em **Python + PyQt6 + QML**. Projeto independente dos dois originais.

## Executar no Windows

Requer Python 3.10 ou superior. O Comix também precisa de Chrome/Chromium instalado.

```powershell
cd E:\Desktop\Mangadex\manga-downloader
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

Após instalar, também é possível abrir `start.bat`. Para renderização por software:
`start.bat --cpu`.

Para acompanhar erros no terminal, execute `debug.bat` ou
`.\.venv\Scripts\python -u main.py --debug`. Os logs também ficam em
`logs/comix-downloader.log` dentro do projeto.
Mantenha **Navegador em segundo plano** ativado. Se o Comix pedir **Security check**
ou a verificação automática não terminar, o aplicativo abre uma única janela do Chrome
para você resolver o captcha. Após a liberação, ela fecha e o trabalho continua headless,
reutilizando a sessão. Você tem até cinco minutos; fechar a janela ou cancelar o download
interrompe a tentativa. Se desativar a opção, o navegador fica visível durante todo o trabalho.

## Uso

1. Escolha a plataforma para pesquisar por nome ou navegar pelos destaques.
2. Cole um link `https://comix.to/title/...` ou `https://mangadex.org/title/...`.
   Links são reconhecidos automaticamente, independentemente da plataforma escolhida.
3. Em **Settings → MangaDex — idioma dos capítulos**, escolha o idioma (padrão: português brasileiro).
   Alterar o idioma recarrega a obra aberta e limpa a seleção anterior.
4. Selecione capítulos e grupo de tradução e clique em **DOWNLOAD**. Revise o título,
   autor e capa preenchidos pelo site; **Trocar capa** permite escolher uma imagem local.
5. Escolha **PDF** ou **EPUB** para reunir todos os capítulos selecionados em um único
   livro. A capa é incorporada ao livro e o título e autor ficam gravados nos metadados.
   O nome do arquivo é o título definido, com caracteres inválidos para Windows substituídos.
   Imagens e CBZ continuam disponíveis por capítulo.
6. Adicione os capítulos à fila e acompanhe o progresso na aba Downloads; cancelar interrompe o trabalho
   nos pontos de verificação, depois que as requisições em andamento terminarem.

Os arquivos ficam em `downloads` dentro deste projeto, salvo alteração nas configurações.
O livro único fica diretamente na pasta de destino, como `Título escolhido.pdf` ou
`Título escolhido.epub`. Ele só é publicado depois que todos os capítulos estão completos.
Durante a geração, o card informa **Gerando o livro completo**. As páginas ficam preservadas
na pasta temporária para retomar falhas; após publicar, são mantidas apenas se **Manter páginas**
estiver ativo. Para imagens e CBZ do MangaDex, as pastas incluem a plataforma e o ID da obra.
As configurações usam uma pasta própria: `%APPDATA%\manga-downloader` no Windows.

### Histórico, seleção e fila

- Ao clicar na busca, aparecem as últimas **50 pesquisas e obras abertas**, sem
  duplicatas e com as mais recentes primeiro. Clique em um item para repetir a
  busca na plataforma original ou reabrir o mangá. O histórico começa a ser
  registrado a partir desta versão.
- Em detalhes, digite **1-50** e clique em **Selecionar intervalo**. Os limites
  são inclusivos, aceitam decimais e respeitam o filtro de grupo atual. A seleção
  das linhas visíveis é substituída pelo intervalo; linhas ocultas preservam sua seleção.
- Downloads são organizados em **cards por mangá**, com total de capítulos,
  concluídos, restantes e falhas. Apenas um card fica expandido por vez.
- Os cards do MangaDex mostram quantos capítulos concluídos estão no idioma escolhido
  e quantos usam o fallback em inglês. O idioma é salvo junto com o download, sem mudar
  quando as preferências forem alteradas. **Abrir mangá** volta à página de detalhes.
- Cada solicitação entra na fila com uma cópia de suas configurações. Um mangá
  é processado por vez; seus capítulos usam o paralelismo configurado.
- O histórico e a fila persistem ao fechar. Trabalhos que aguardavam na fila
  continuam ao reabrir; o trabalho interrompido fica disponível para **Retomar
  pendentes**, sem repetir capítulos já concluídos. O capítulo interrompido é
  retomado reutilizando páginas completas quando **Manter páginas** estava ativo.
- **Cancelar** funciona para o item ativo ou para itens na fila. **Limpar
  finalizados** remove registros concluídos, cancelados ou com falha; não apaga arquivos.

Além de `config.json`, a pasta de configurações contém `search-history.json` e
`downloads.json`. As gravações usam substituição atômica para evitar arquivos
parciais. Fechar durante um download aguarda a interrupção do trabalho em andamento.

### Opções de leitura e pasta

Em **Settings**, **Escolher pasta...** abre o seletor nativo de diretórios do sistema.
O painel tem rolagem para acessar todas as opções.

- **Tipo de leitura:** na janela **Preparar download**, junto de título, autor,
  capa e formato, escolha **Mangá — páginas separadas** ou **Tira longa — leitura
  vertical** ao gerar PDF. A escolha vale para aquele download. Tiras muito longas
  são divididas para limitar o tamanho de cada página.
- **PDF — largura:** `0` usa a maior largura do capítulo; também aceita de 600 a
  4000 pixels. Essas opções de PDF funcionam nas duas plataformas.
- **MangaDex — imagens comprimidas:** usa as páginas Data Saver do MangaDex.
- **MangaDex — completar com inglês:** inclui capítulos em inglês onde não há
  tradução do mesmo volume/capítulo no idioma escolhido. Recarregue a obra depois
  de alterar a opção. Capítulos sem número não são associados entre idiomas.

PDF e EPUB reúnem os capítulos selecionados em um livro único, com título, autor
e capa definidos na janela de preparação. CBZ continua separado por capítulo.

### Pasta de arquivos temporários

Em **Settings → Arquivos temporários**, escolha uma pasta pelo seletor do sistema
ou digite o caminho. A preferência fica salva entre sessões. O padrão é a subpasta
`manga-downloader` dentro da pasta temporária do sistema.

- As páginas são gravadas em uma subpasta exclusiva por capítulo; PDF/CBZ são
  montados nela antes de serem transferidos para **Download Path**.
- Essa subpasta é removida ao concluir, falhar ou cancelar normalmente. Um
  encerramento forçado do processo pode deixar arquivos nessa pasta.
- **Manter páginas e reutilizar ao retomar** grava cada página completa em
  `retained-pages` dentro da pasta temporária. Esse cache sobrevive a falhas,
  cancelamentos e reinícios do app. Ao tentar novamente, páginas válidas são
  reutilizadas; páginas ausentes ou corrompidas são baixadas novamente. O cache
  distingue plataforma, obra, capítulo, qualidade e lista de páginas.
- Após conversão, a opção também preserva cópias das páginas no destino.
  Desativar a opção não apaga páginas já retidas.
- **Limpar todas as páginas baixadas**, em Settings, apaga páginas registradas
  por esta versão: cache, cópias após conversão e saídas no formato imagens,
  inclusive em pastas usadas anteriormente. PDF/CBZ são preservados. Arquivos
  antigos não registrados e arquivos pessoais não são varridos nem removidos.
  Aguarde ou cancele os downloads em andamento antes de limpar.
- Trabalhos já adicionados à fila mantêm sua pasta de páginas/conversão original.
  Novos temporários das bibliotecas e novos perfis do navegador usam a preferência
  atual; perfis já abertos continuam no local anterior.
- Temporários e downloads podem ficar em unidades diferentes. Nesse caso, a
  publicação final usa brevemente um arquivo `.part` no destino para preservar
  um arquivo anterior se a cópia falhar. Gravações atômicas das configurações e
  históricos também usam pequenos temporários ao lado dos respectivos arquivos.

## Escopo

- Busca paginada, capas, detalhes, capítulos e download nas duas plataformas.
- MangaDex usa API pública, feed paginado e MangaDex@Home, adaptados do projeto
  `mangadex-downloader`; não abre navegador para acessar essa plataforma.
- Capítulos externos ou sem páginas não são oferecidos para download.
- A busca exibe conteúdo classificado como seguro/sugestivo, como na base Comix.
- Links de capítulos/listas, login, biblioteca pessoal, EPUB e demais recursos
  exclusivos do antigo MangaDex downloader não foram migrados.
- Comix mantém o acesso via navegador e depende da disponibilidade do site e da
  conclusão da verificação Cloudflare. O modo de navegador pode ser alterado nas configurações.
- A interface mantém os textos em inglês da base, com os seletores de idioma adicionados.

## Estrutura e testes

### Ritmo de downloads do Comix

Se a extração de um capítulo vier incompleta, as páginas disponíveis são baixadas
e preservadas por seu número original. Uma nova tentativa busca as páginas que
faltam. Ao retomar manualmente, páginas salvas e válidas são reaproveitadas, mesmo
com “manter imagens” desativado; nesse caso, são removidas após o capítulo ser
salvo com sucesso. O navegador pode reabrir o leitor para obter os links, mas o
downloader não repete as imagens já salvas.

Ao finalizar um PDF combinado, a fila faz uma rodada extra nos capítulos que
falharam, com orçamento de 60 segundos por capítulo e 3 minutos para a rodada.
A tela identifica o capítulo atual. Ao atingir o limite, cancela a espera pelo
navegador e segue para a geração com o conteúdo salvo, sem cancelar o livro.
Se ainda houver falhas, gera o PDF com as páginas disponíveis, na ordem
original, e mostra “PDF gerado com falhas” com a quantidade de páginas ausentes.
Capítulos cujo total não pôde ser identificado são informados separadamente.
As páginas temporárias são preservadas para uma retomada posterior. Sem nenhuma
página disponível, não é possível gerar o PDF. Cancelamento e bloqueio do Comix
impedem novas tentativas; o bloqueio permite gerar o PDF com o material já salvo.

O Comix processa um capítulo por vez, com uma pausa mínima de 5 segundos entre
o término de um capítulo e a próxima extração. Requisições diretas de imagens
compartilham um intervalo mínimo de 500 ms, inclusive nas tentativas repetidas.
Esses valores são precauções locais, não limites publicados pelo site. O
pré-carregamento de imagens dentro do navegador não é abrangido pelo intervalo
do downloader; um capítulo ainda pode gerar várias requisições no navegador.

Uma resposta HTTP 403/429 ou uma página explícita de bloqueio pausa os downloads
do Comix, sem abrir outra janela para tentar CAPTCHA. A fila preserva os capítulos
concluídos e exige retomada manual. A espera mínima é de 60 segundos, ou o prazo
maior indicado em `Retry-After` (segundos ou data HTTP). A fila salva esse prazo
para respeitá-lo ao reabrir o aplicativo. Isso reduz a insistência após recusa,
mas não garante que o site nunca bloqueie o acesso.

`src/api/providers.py` resolve links e escolhe o adaptador; `src/api/mangadex.py`
implementa MangaDex; `src/api/comix.py` mantém Comix. O motor de download e os
formatos são compartilhados; `gui/bridge` conecta Python aos componentes de `gui/qml`.
O ponto de entrada suportado é `main.py` (GUI); `src/cli` preserva o código legado Comix.

```powershell
.\.venv\Scripts\python -m pip install pytest
.\.venv\Scripts\python -m pytest -q
```

Os testes usam respostas simuladas, sem depender dos sites. A disponibilidade real
dos serviços e seus mecanismos de proteção exige validação online separada.

Validação inicial: 80 testes passaram, incluindo a abertura da janela completa.
Também foram verificados online busca, detalhes, capítulos em português brasileiro,
MangaDex@Home e download de uma página em memória. O fluxo real do Comix via
navegador não foi executado nesta validação.

## Créditos

- Interface, acesso ao Comix e motor de download: comix-downloader, Yui007, MIT (`LICENSE`).
- Fluxo de feed, metadados e MangaDex@Home: mangadex-downloader, Rahman Yusuf e
  contribuidores, MIT (`licenses/mangadex-downloader.LICENSE`).

As pastas dos projetos originais não são necessárias para executar esta aplicação.
