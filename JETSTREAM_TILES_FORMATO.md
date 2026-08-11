# Banco de dados climático - NexAtlas JetStream

Este repositório demonstra como ler tiles de previsão climática no formato `JetStreamDataTile`.

O objetivo do documento é dar contexto ao leitor sobre:

1. como os dados de previsão chegam ao banco climático;
2. como eles são organizados no Object Storage;
3. como `read-tiles.js` localiza um tile e lê os valores numéricos de um pixel.

## Pipeline de dados

![Arquitetura de alto nível do Zephyr](./zephyr-data-high-level.png)

O modelo ECMWF publica novas previsões periodicamente em arquivos brutos voltados principalmente para análise meteorológica e processamento técnico. Esses arquivos costumam ser grandes, usam formatos como GRIB e GRIB2, são pouco convenientes para aplicações de usuário final e podem conter múltiplos datasets dentro de um mesmo arquivo.

O Zephyr existe para transformar esse material bruto em datasets prontos para consumo. Ele baixa os arquivos publicados pelo ECMWF, extrai as variáveis de interesse, separa os dados em pacotes específicos e os reorganiza como tiles endereçáveis por tempo, nível vertical e posição espacial.

O resultado continua útil para análise, mas passa a ter uma estrutura mais adequada para aplicações: cada dataset tem um propósito claro, os arquivos são menores e a leitura de um ponto geográfico não exige interpretar diretamente GRIB/GRIB2 nem carregar um arquivo meteorológico completo.

O formato de tiles é o que torna esse pacote prático para aplicações. Em vez de abrir um arquivo grande e procurar a informação dentro dele, a aplicação calcula diretamente qual tile contém a coordenada desejada, lê apenas esse arquivo pequeno e acessa o pixel correspondente em memória. Isso reduz overhead de disco, rede e processamento.

O uso de FlatBuffer reforça esse objetivo. Cada tile é um payload binário compacto que pode ser transferido para o cliente em chunks, como qualquer objeto binário em HTTP, sem conversão para JSON ou outro formato textual. Depois que os bytes necessários do tile estão disponíveis, o leitor acessa o buffer diretamente, sem uma etapa de desserialização pesada para reconstruir objetos intermediários.

Em alto nível:

- o ECMWF publica arquivos de previsão, normalmente a cada 6 horas;
- o Zephyr baixa os arquivos brutos de entrada, em formatos como GRIB ou GRIB2;
- em horários definidos, o Zephyr processa as variáveis relevantes e separa os datasets de interesse;
- o resultado é enviado para o Object Storage em pacotes `fb` ou `gz.zip`, organizados por dataset;
- cada pacote contém tiles endereçáveis por tempo, nível vertical e posição espacial;
- cada tile contém um bloco binário simples de amostras, pensado para consulta direta em disco, rede e memória.

Este repositório não implementa o Zephyr. Ele parte do resultado já produzido por esse pipeline e demonstra como ler os dados armazenados.

## Estrutura no Object Storage

![Estrutura de pastas e arquivos](./zephyr-data-format-structure.png)

No banco climático, os dados são armazenados em Object Storage, hoje em DigitalOcean Spaces. A estrutura lógica é organizada por região, dataset, tempo, nível vertical e tile:

```txt
nexatlas-jetstream/[region]/[dataset]_{fb|gz.zip}/[timestamp]/[level]/[z]/[x]/[y].{fb|gz}
```

Na prática, existem duas formas físicas equivalentes para o pacote, voltadas a modos de uso diferentes:

```txt
nexatlas-jetstream/[region]/[dataset]_fb/[timestamp]/[level]/[z]/[x]/[y].fb
nexatlas-jetstream/[region]/[dataset]_gz.zip/[timestamp]/[level]/[z]/[x]/[y].gz
```

O pacote `_fb` é a forma online, pensada para consulta direta no bucket: a aplicação monta o caminho do tile necessário e lê apenas aquele objeto. Como o tile é um FlatBuffer binário, ele pode ser transferido em chunks para o cliente e interpretado diretamente quando os bytes do tile estiverem disponíveis, com pouco overhead.

O pacote `_gz.zip` é a forma comprimida, pensada para distribuição e uso offline. O pacote inteiro pode ser baixado, extraído localmente e consultado com a mesma estrutura de diretórios, mas usando tiles individuais `.gz`.

`nexatlas-jetstream` é tanto a divisão lógica do banco climático quanto o bucket do DigitalOcean Spaces onde os dados ficam armazenados. A partir dele:

- `region`: região geográfica à qual o dado se aplica;
- `dataset`: variável ou conjunto de variáveis climáticas armazenadas;
- `timestamp`: instante da previsão;
- `level`: nível vertical aproximado;
- `z`, `x`, `y`: endereço espacial do tile no padrão Tiled Web Map;
- `.fb` ou `.gz`: formato físico do arquivo do tile.

### `region`

Identifica a área do globo coberta pelo pacote.

Para países, usa código ISO 3166-1 alpha-3 em letras minúsculas, como:

```txt
bra
chl
```

Para continentes, usa a extensão adotada pela OTAN, como `srr` para América do Sul. Também pode existir um pacote global:

```txt
global
```

### `dataset`

Identifica o dado armazenado e o formato do pacote.

Exemplos:

```txt
wind_fb
wind_gz.zip
cloud_coverage_fb
```

O sufixo indica como os tiles foram empacotados:

- `_fb`: tiles FlatBuffer sem compressão, publicados para leitura online direto do bucket;
- `_gz.zip`: pacote `.zip` contendo tiles FlatBuffer comprimidos individualmente com gzip, destinado a download e uso offline.

Depois de extraído, um pacote `wind_gz.zip` aparece localmente como uma pasta de dataset, por exemplo `wind_gz`, contendo tiles `.gz`.

### `timestamp`

É o instante relevante da previsão, em segundos desde `1970-01-01T00:00:00Z` no formato Unix time.

Um mesmo dataset pode conter vários timestamps, porque uma rodada de previsão produz múltiplos horizontes de tempo.

Exemplo:

```txt
1777528800
```

### `level`

Representa o nível aproximado de altitude em pés.

Esse valor é estimado a partir da pressão atmosférica, considerando `1013,2 hPa` como referência.

Exemplos:

```txt
0
1000
1700
51800
```

No schema FlatBuffer, esses valores aparecem no campo `MetaData.altitudes`. No caminho do banco e na saída do leitor, eles são tratados como `level`.

### `z`, `x`, `y`

São os índices do padrão Tiled Web Map:

```txt
[z]/[x]/[y].{fb|gz}
```

- `z`: nível de zoom;
- `x`: coluna do tile;
- `y`: linha do tile.

No schema FlatBuffer, os valores disponíveis de `z` aparecem em `MetaData.zooms`.

## Escopo deste repositório

O script `read-tiles.js` lê dados a partir da pasta de um dataset já disponível localmente.

Ou seja, ele não recebe o caminho completo:

```txt
nexatlas-jetstream/[region]/[dataset]_{fb|gz.zip}
```

Ele recebe apenas a pasta do dataset extraído:

```txt
[dataset]_{fb|gz}
```

No exemplo local:

```txt
wind_gz
```

A partir dessa pasta, o script espera encontrar:

```txt
metadata.fb
[timestamp]/[level]/[z]/[x]/[y].{fb|gz}
```

## Fluxo de leitura

`read-tiles.js` demonstra o fluxo essencial para ler um ponto geográfico:

1. ler `metadata.fb`;
2. selecionar `timestamp`, `level` e `z`;
3. converter longitude/latitude para tile XYZ e pixel dentro do tile;
4. montar o caminho `[timestamp]/[level]/[z]/[x]/[y]`;
5. ler o tile `.fb` ou `.gz`;
6. interpretar `Tile.data` conforme o tipo numérico descrito no metadata;
7. aplicar a escala fixed-point;
8. retornar os valores dos canais do pixel.

## Metadata e tile

### `metadata.fb`

O arquivo `metadata.fb` fica na raiz do dataset e descreve como localizar e interpretar os tiles.

Campos relevantes:

- `zooms`: valores disponíveis de `z`;
- `altitudes`: valores disponíveis de `level`;
- `timestamps`: instantes disponíveis;
- `width` e `height`: dimensões de cada tile, em pixels;
- `channel_count`: quantidade de canais por pixel;
- `data_type`: tipo inteiro usado para armazenar as amostras;
- `fixed_point_precision`: quantidade de casas decimais usadas na escala fixed-point;
- `type`: formato esperado dos tiles, como `fb` ou `fb-gz`.

O script lê esse arquivo como `JetStreamDataTile.MetaData`.

### Tile individual

Cada tile é um FlatBuffer do tipo `JetStreamDataTile.Tile`.

O campo principal é:

```txt
Tile.data: [uint8]
```

Esse vetor é um bloco bruto de bytes. Ele não deve ser lido como valores `uint8` finais. O tipo real de cada amostra vem de `MetaData.data_type`.

### Código gerado

O arquivo `JetStreamDataTile_generated.js` é gerado a partir de `JetStreamDataTile.fbs`, que define o contrato binário do formato.

Pela documentação oficial do FlatBuffers, o fluxo é:

1. escrever um schema `.fbs`;
2. usar o compilador `flatc` para gerar código na linguagem desejada;
3. importar o código gerado na aplicação;
4. usar as classes geradas para serializar ou desserializar buffers FlatBuffer.

Para JavaScript, o comando equivalente é:

```bash
flatc --js JetStreamDataTile.fbs
```

Esse comando gera as classes usadas pelo leitor, como:

```js
JetStreamDataTile.MetaData.getRootAsMetaData(...)
JetStreamDataTile.Tile.getRootAsTile(...)
```

O código gerado não deve ser editado manualmente. Mudanças no formato devem ser feitas em `JetStreamDataTile.fbs` e depois propagadas gerando novamente `JetStreamDataTile_generated.js` com `flatc`.

## Seleção do tile

A variante lida pelo script é escolhida a partir do metadata:

- `z`: maior zoom disponível;
- `level`: valor pedido, se existir; caso contrário, maior valor disponível;
- `timestamp`: valor pedido, se existir; caso contrário, timestamp mais próximo do horário atual.

Quando `timestamp` ou `level` são informados, a correspondência precisa ser exata com os valores do metadata.

Depois da seleção, o caminho do tile é:

```txt
<dataset_folder>/<timestamp>/<level>/<z>/<x>/<y>.(fb|gz)
```

Exemplo:

```txt
wind_gz/1777507200/0/2/0/1.gz
```

Arquivos `.gz` são descomprimidos antes do parse FlatBuffer.

## Conversão geográfica para tile e pixel

A coordenada geográfica é convertida para a grade XYZ usando Web Mercator.

Para longitude:

```js
x = ((longitude + 180) / 360) * tilesPerAxis;
```

Para latitude:

```js
latitudeRadians = (latitude * Math.PI) / 180;
mercatorY =
  Math.log(Math.tan(latitudeRadians) + 1 / Math.cos(latitudeRadians)) /
  Math.PI;
y = ((1 - mercatorY) / 2) * tilesPerAxis;
```

Onde:

```js
tilesPerAxis = 1 << z;
```

A parte inteira de `x` e `y` define o tile:

```js
tileX = Math.floor(x);
tileY = Math.floor(y);
```

A parte fracionária define o pixel dentro do tile:

```js
pixelX = Math.floor((x - tileX) * width);
pixelY = Math.floor((y - tileY) * height);
```

Antes de ler os dados, o script valida:

```txt
0 <= pixelX < width
0 <= pixelY < height
```

## Interpretação dos valores

O schema define `Tile.data` como `[uint8]`, mas o metadata informa como esses bytes devem ser reinterpretados:

```txt
int8  -> Int8Array
int16 -> Int16Array
int32 -> Int32Array
```

Os dados são armazenados em ordem row-major, com canais intercalados por pixel:

```txt
pixel 0 canal 0
pixel 0 canal 1
pixel 1 canal 0
pixel 1 canal 1
...
```

O índice de uma amostra é:

```txt
(pixelY * width + pixelX) * channelCount + channelIndex
```

Esse cálculo transforma a posição bidimensional do pixel e o canal desejado em uma posição linear no array tipado.

## Por que armazenar dessa forma

O tile guarda as amostras numéricas como um bloco contínuo de bytes para manter o arquivo compacto e simples de percorrer.

Como todos os pixels de um mesmo tile compartilham o mesmo tipo numérico, não é necessário repetir essa informação em cada valor. `MetaData.data_type` descreve uma vez como o bloco inteiro deve ser interpretado.

Essa separação permite escolher o menor tipo inteiro suficiente para cada conjunto de dados. Um tile pode usar `int8`, `int16` ou `int32`, reduzindo o tamanho dos arquivos quando uma precisão maior não é necessária.

Além disso, os valores são armazenados como inteiros em fixed-point, em vez de ponto flutuante. Isso reduz o volume de bytes, preserva uma precisão decimal conhecida e torna a leitura previsível.

O formato separa:

- os bytes brutos do tile, em `Tile.data`;
- o tipo usado para reinterpretar esses bytes, em `MetaData.data_type`;
- a escala decimal aplicada aos inteiros, em `MetaData.fixed_point_precision`.

## Escala fixed-point

As amostras são armazenadas como inteiros em fixed-point. A escala vem de:

```txt
MetaData.fixed_point_precision
```

O valor final de cada canal é:

```js
sample / 10 ** fixedPointPrecision
```

Exemplo:

```txt
sample = 1234
fixedPointPrecision = 2
valor final = 12.34
```

## Resumo vetorial

Quando há pelo menos dois canais, o script também calcula um resumo vetorial usando os dois primeiros valores como componentes `u` e `v`:

```js
magnitude = Math.hypot(u, v);
angleDegrees = normalizeDegrees(90 - radiansToDegrees(Math.atan2(v, u)));
```

Essa convenção é útil para dados como vento. O formato `JetStreamDataTile` apenas armazena canais numéricos; ele não define semanticamente que os dois primeiros canais representam vento.

## Referências

- FlatBuffers: https://flatbuffers.dev/
- FlatBuffers Compiler (`flatc`): https://flatbuffers.dev/flatc/
- FlatBuffers JavaScript: https://flatbuffers.dev/languages/javascript/
- Tiled Web Map: https://en.wikipedia.org/wiki/Tiled_web_map
- OpenStreetMap Slippy Map Tilenames: https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames
