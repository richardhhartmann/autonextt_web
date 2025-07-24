import pyodbc
from flask import Flask, render_template, request, redirect, session, flash, jsonify
from datetime import datetime
import json

app = Flask(__name__)
app.secret_key = 'chave_secreta_segura'

# --- Configurações do Banco de Dados ---
DB_SERVER = 'localhost'
DB_DATABASE = 'NexttLoja'

def conectar_sqlserver():
    """Cria e retorna uma conexão com o banco de dados SQL Server."""
    try:
        conn_str = (f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};Trusted_Connection=yes;TrustServerCertificate=yes;')
        conn = pyodbc.connect(conn_str, autocommit=False)
        return conn
    except pyodbc.Error as ex:
        print(f"Erro de conexão com o banco de dados: {ex}")
        return None

def rows_to_dicts(cursor):
    """Converte pyodbc.Row para uma lista de dicionários."""
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def r_form(key, default=None):
    """Helper para obter dados do formulário com um padrão, tratando strings vazias."""
    val = request.form.get(key)
    return val if val not in [None, ''] else default

# --- Rotas de Autenticação e Dashboard ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'usuario_logado' in session: return redirect('/dashboard')
    if request.method == 'POST':
        login_usuario, senha = request.form['login'], request.form['senha']
        conn = conectar_sqlserver()
        if conn:
            user = None
            try:
                cur = conn.cursor()
                cur.execute("SELECT usu_codigo, usu_login FROM tb_usuario WHERE usu_login = ? AND usu_senha = ?", (login_usuario, senha))
                user = cur.fetchone()
            except pyodbc.Error as e: flash(f'Erro ao autenticar: {e}', 'danger')
            finally: conn.close()

            if user:
                session['usuario_logado'], session['usuario_codigo'] = user.usu_login, user.usu_codigo
                flash('Login realizado com sucesso!', 'success')
                return redirect('/dashboard')
            else: flash('Login ou senha inválidos.', 'danger')
        else: flash('Não foi possível conectar ao banco de dados.', 'danger')
        return redirect('/')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Você foi desconectado.', 'info')
    return redirect('/')

@app.route('/dashboard')
def dashboard():
    if 'usuario_logado' not in session: return redirect('/')
    return render_template('dashboard.html')

# --- Rotas de Produtos ---
@app.route('/produtos')
def visualizar_produtos():
    if 'usuario_logado' not in session: return redirect('/')
    colunas = ['Código Produto', 'Seção', 'Espécie', 'Descrição', 'Marca', 'Referência', 'Cor', 'Tamanho', 'Ativo']
    return render_template('visualizar_produtos.html', colunas=colunas)

@app.route('/api/produtos')
def api_produtos():
    """
    Rota para obter produtos paginados, com ordenação e busca dinâmica (incluindo marca).
    """
    if 'usuario_logado' not in session:
        return jsonify({"error": "Não autorizado"}), 401

    try:
        # --- Parâmetros da Requisição ---
        page = request.args.get('page', 1, type=int)
        search_term = request.args.get('search', '', type=str)
        sort_by = request.args.get('sort_by', 'Descrição')
        sort_order = request.args.get('sort_order', 'asc')

        per_page = 100
        offset = (page - 1) * per_page

        # --- Segurança: Whitelist de Colunas para Ordenação ---
        allowed_sort_columns = ['Código Produto', 'Seção', 'Espécie', 'Descrição', 'Marca', 'Referência']
        if sort_by not in allowed_sort_columns:
            sort_by = 'Descrição'

        if sort_order.lower() not in ['asc', 'desc']:
            sort_order = 'asc'

        # --- Conexão com o Banco ---
        conn = conectar_sqlserver()
        if not conn:
            return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

        cursor = conn.cursor()

        # --- Montagem da Query Dinâmica ---
        params = []
        where_clauses = []

        if search_term:
            like_term = f"%{search_term}%"
            # Adicionada a busca por marca (m.mar_descricao)
            search_logic = """
            (
                p.prd_descricao LIKE ? OR
                m.mar_descricao LIKE ? OR 
                p.prd_referencia_fornec LIKE ? OR
                p.prd_codigo_original LIKE ? OR
                dbo.fn_formata_codigo_produto(p.sec_codigo, p.esp_codigo, p.prd_codigo) LIKE ?
            )
            """
            where_clauses.append(search_logic)
            # Adicionado um parâmetro 'like_term' a mais para a busca por marca
            params.extend([like_term, like_term, like_term, like_term, like_term])

        sql_where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
        params.extend([offset, per_page])
        
        order_by_clause = f"ORDER BY [{sort_by}] {sort_order}, p.prd_codigo"
        
        # ATENÇÃO: A alteração está na linha que seleciona "p.prd_ultimo_custo".
        query = f"""
            SELECT
                dbo.fn_formata_codigo_produto(p.sec_codigo, p.esp_codigo, p.prd_codigo) AS [Código Produto],
                p.sec_codigo,
                p.esp_codigo,
                p.prd_codigo,
                s.sec_descricao AS [Seção],
                e.esp_descricao AS [Espécie],
                p.prd_descricao AS [Descrição],
                m.mar_descricao AS [Marca],
                p.prd_referencia_fornec AS [Referência],
                p.prd_ultimo_custo AS Custo,
                '' AS [Cor], 
                '' AS [Tamanho],
                p.prd_ativo AS [Ativo]
            FROM 
                tb_produto p WITH (NOLOCK)
            INNER JOIN 
                tb_marca m WITH (NOLOCK) ON m.mar_codigo = p.mar_codigo
            INNER JOIN 
                tb_secao s WITH (NOLOCK) ON s.sec_codigo = p.sec_codigo
            INNER JOIN 
                tb_especie e WITH (NOLOCK) ON e.sec_codigo = p.sec_codigo AND e.esp_codigo = p.esp_codigo
            {sql_where}
            {order_by_clause}
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;
        """

        cursor.execute(query, params)
        produtos = rows_to_dicts(cursor)
        
        return jsonify(produtos)

    except pyodbc.Error as e:
        app.logger.error(f"Database error in /api/produtos: {str(e)}")
        return jsonify({"error": "Erro ao carregar produtos", "details": str(e)}), 500
        
    except Exception as e:
        app.logger.error(f"Unexpected error in /api/produtos: {str(e)}")
        return jsonify({"error": "Erro inesperado", "details": str(e)}), 500
        
    finally:
        if conn:
            conn.close()

def get_db_connection_and_cursor():
    """Helper para obter conexão e cursor."""
    conn = conectar_sqlserver()
    if not conn:
        raise Exception("Falha na conexão com o banco de dados")
    return conn, conn.cursor()

@app.route('/api/produtos/form_data')
def api_get_product_form_data():
    """Fornece todos os dados necessários para popular os comboboxes do formulário de produto."""
    if 'usuario_logado' not in session:
        return jsonify({"error": "Não autorizado"}), 401
    
    conn = None
    try:
        conn, cursor = get_db_connection_and_cursor()
        
        # Seções
        cursor.execute("SELECT sec_codigo, sec_descricao FROM tb_secao ORDER BY sec_descricao")
        secoes = rows_to_dicts(cursor)
        
        # Marcas
        cursor.execute("SELECT mar_codigo, mar_descricao FROM tb_marca ORDER BY mar_descricao")
        marcas = rows_to_dicts(cursor)

        # Usuários (Compradores)
        cursor.execute("SELECT usu_codigo, usu_login FROM tb_usuario WHERE usu_ativo = 1 ORDER BY usu_login")
        compradores = rows_to_dicts(cursor)

        # Unidades
        cursor.execute("SELECT und_codigo, und_descricao FROM tb_unidade ORDER BY und_descricao")
        unidades = rows_to_dicts(cursor)

        # Classificação Fiscal
        cursor.execute("SELECT clf_codigo, clf_codigo_fiscal, clf_descricao FROM tb_classificacao_fiscal ORDER BY clf_descricao")
        classificacoes = rows_to_dicts(cursor)

        # Etiquetas
        cursor.execute("SELECT etq_codigo, etq_descricao FROM tb_etiqueta ORDER BY etq_descricao")
        etiquetas = rows_to_dicts(cursor)

        # Origem (valores fixos)
        origens = [
            {"codigo": "0", "descricao": "0 - Nacional"},
            {"codigo": "1", "descricao": "1 - Estrangeira, Importação direta"},
            {"codigo": "2", "descricao": "2 - Estrangeira, adquirida no mercado interno"},
            {"codigo": "3", "descricao": "3 - Nacional, Mercadoria ou Bem"},
            {"codigo": "4", "descricao": "4 - Nacional, produção conforme processos básicos"},
            {"codigo": "5", "descricao": "5 - Nacional, com Conteúdo de Importação <= 40%"},
            {"codigo": "6", "descricao": "6 - Estrangeira, importação direta, sem similar nacional"},
            {"codigo": "7", "descricao": "7 - Estrangeira, adquirida no mercado interno, sem similar nacional"}
        ]

        return jsonify({
            "secoes": secoes,
            "marcas": marcas,
            "compradores": compradores,
            "unidades": unidades,
            "classificacoes_fiscais": classificacoes,
            "etiquetas": etiquetas,
            "origens": origens
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/especies_por_secao/<int:sec_codigo>')
def api_get_especies_por_secao(sec_codigo):
    """Retorna as espécies para uma dada seção."""
    if 'usuario_logado' not in session: return jsonify({"error": "Não autorizado"}), 401
    conn = None
    try:
        conn, cursor = get_db_connection_and_cursor()
        cursor.execute("SELECT esp_codigo, esp_descricao FROM tb_especie WHERE sec_codigo = ? ORDER BY esp_descricao", sec_codigo)
        especies = rows_to_dicts(cursor)
        return jsonify(especies)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/atributos_por_especie/<int:sec_codigo>/<int:esp_codigo>')
def api_get_atributos_por_especie(sec_codigo, esp_codigo):
    """Retorna os tipos de atributos permitidos para uma dada seção/espécie."""
    if 'usuario_logado' not in session: return jsonify({"error": "Não autorizado"}), 401
    conn = None
    try:
        conn, cursor = get_db_connection_and_cursor()
        # Busca os tpa_codigo permitidos pela regra, excluindo Cor e Tamanho
        query = """
            SELECT rae.tpa_codigo, tpa.tpa_descricao 
            FROM tb_regra_atributo_especie rae
            JOIN tb_tipo_atributo tpa ON rae.tpa_codigo = tpa.tpa_codigo
            WHERE rae.sec_codigo = ? AND rae.esp_codigo = ? AND rae.tpa_codigo NOT IN (1, 2)
            ORDER BY tpa.tpa_descricao
        """
        cursor.execute(query, sec_codigo, esp_codigo)
        tipos_atributo = rows_to_dicts(cursor)
        return jsonify(tipos_atributo)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/api/produto/validar_existencia', methods=['POST'])
def api_validar_produto_existente():
    """Verifica se um produto com a mesma marca e referência já existe."""
    if 'usuario_logado' not in session: return jsonify({"error": "Não autorizado"}), 401
    
    data = request.get_json()
    mar_codigo = data.get('mar_codigo')
    prd_referencia_fornec = data.get('prd_referencia_fornec')

    if not mar_codigo or not prd_referencia_fornec:
        return jsonify({'existe': False})

    conn = None
    try:
        conn, cursor = get_db_connection_and_cursor()
        cursor.execute("SELECT 1 FROM tb_produto WHERE mar_codigo = ? AND prd_referencia_fornec = ?",
                       (mar_codigo, prd_referencia_fornec))
        produto_existe = cursor.fetchone() is not None
        return jsonify({'existe': produto_existe})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/produtos/cadastrar/lote')
def cadastrar_produto_lote():
    if 'usuario_logado' not in session:
        return redirect('/')
    
    conn = conectar_sqlserver()
    marcas = []
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT mar_codigo, mar_descricao FROM tb_marca ORDER BY mar_descricao")
            marcas = rows_to_dicts(cursor)
        except pyodbc.Error as e:
            flash(f"Erro ao carregar dados de apoio: {e}", "danger")
        finally:
            conn.close()
            
    return render_template('produto_form_lote.html', marcas=marcas)


# Adicione esta nova rota de API para salvar os produtos em lote
@app.route('/api/produtos/cadastrar/lote', methods=['POST'])
def api_cadastrar_produto_lote():
    if 'usuario_logado' not in session:
        return jsonify({"error": "Não autorizado"}), 401

    data = request.get_json()
    produtos_para_cadastrar = data.get('produtos')

    if not produtos_para_cadastrar:
        return jsonify({"error": "Nenhum produto enviado para cadastro"}), 400

    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn:
            raise Exception("Não foi possível conectar ao banco de dados.")
        
        cursor = conn.cursor()
        usu_codigo = session.get('usuario_codigo')
        
        # Inicia uma transação. Se um produto falhar, todos os anteriores são desfeitos.
        conn.autocommit = False 
        
        for produto in produtos_para_cadastrar:
            # Limpa tabelas aux para cada produto do lote
            # (Assumindo que a procedure as utiliza)
            cursor.execute("DELETE FROM aux_item_produto WHERE usu_codigo = ?", usu_codigo)
            
            # NOTA: Se a sua grade (cor x tamanho) for complexa, a lógica para popular 
            # a 'aux_item_produto' a partir do 'produto.grade' (que viria do modal) iria aqui.
            # Por simplicidade, vamos criar um item padrão.
            cursor.execute("""
                INSERT INTO aux_item_produto(usu_codigo, sec_codigo, esp_codigo, prd_codigo, ipr_codigo) 
                VALUES (?, ?, ?, ?, ?)
            """, usu_codigo, produto.get('sec_codigo'), produto.get('esp_codigo'), 0, 1)

            # Monta os parâmetros para a procedure pr_produto_i
            params = (
                usu_codigo,
                int(produto.get('sec_codigo')),
                int(produto.get('esp_codigo')),
                0, # prd_codigo, a procedure irá gerar
                produto.get('prd_descricao'),
                produto.get('prd_descricao_reduzida'),
                int(produto.get('mar_codigo')) or None,
                datetime.now(),
                produto.get('prd_unidade'),
                None, None, 0.0, 0.0, 0.0, 0.0, # datas e custos padrão
                produto.get('prd_codigo_original'),
                1, # prd_ativo
                produto.get('prd_arquivo_foto'),
                produto.get('prd_referencia_fornec'),
                produto.get('clf_codigo'),
                int(produto.get('usu_codigo_comprador', 0)) or None,
                int(produto.get('und_codigo', 0)) or None,
                float(produto.get('prd_valor_venda', 0.0)) or None,
                produto.get('prd_origem'),
                float(produto.get('prd_percentual_icms', 0.0)) or None,
                float(produto.get('prd_percentual_ipi', 0.0)) or None,
                produto.get('etq_codigo_padrao'),
                1, # permite comprar
                int(produto.get('udc_codigo', 0)) or None,
                float(produto.get('prd_valor_unidade_conversao', 0.0)) or None
            )

            sql = "{CALL pr_produto_i (" + "?,"*29 + "?)}"
            cursor.execute(sql, params)

        # Se tudo correu bem, comita a transação
        conn.commit()
        return jsonify({"success": f"{len(produtos_para_cadastrar)} produto(s) cadastrado(s) com sucesso!"}), 200

    except (pyodbc.Error, ValueError, Exception) as e:
        if conn:
            conn.rollback() # Desfaz todas as operações se uma falhar
        app.logger.error(f"Erro ao cadastrar produtos em lote: {e}")
        return jsonify({"error": f"Erro no servidor: {e}"}), 500
    finally:
        if conn:
            conn.autocommit = True # Restaura o comportamento padrão
            conn.close()

@app.route('/produtos/cadastrar', methods=['GET', 'POST'])
def cadastrar_produto():
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_produto_post()
    
    conn = conectar_sqlserver()
    marcas = []
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT mar_codigo, mar_descricao FROM tb_marca ORDER BY mar_descricao")
            marcas = cursor.fetchall()
        except pyodbc.Error as e: flash(f"Erro ao carregar marcas: {e}", "danger")
        finally: conn.close()

    return render_template('produto_form.html', produto=None, itens_grade='[]', marcas=marcas)

@app.route('/produtos/editar/<int:sec_codigo>/<int:esp_codigo>/<int:prd_codigo>', methods=['GET', 'POST'])
def editar_produto(sec_codigo, esp_codigo, prd_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_produto_post(is_edit=True)

    conn = None
    produto, itens_grade_dict, marcas = None, [], []
    try:
        conn = conectar_sqlserver()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tb_produto WHERE sec_codigo=? AND esp_codigo=? AND prd_codigo=?", (sec_codigo, esp_codigo, prd_codigo))
            produto = cursor.fetchone()
            if not produto:
                flash("Produto não encontrado!", "danger")
                return redirect('/produtos')
            
            cursor.execute("SELECT * FROM tb_item_produto WHERE sec_codigo=? AND esp_codigo=? AND prd_codigo=?", (sec_codigo, esp_codigo, prd_codigo))
            itens_grade_dict = rows_to_dicts(cursor)
            
            cursor.execute("SELECT mar_codigo, mar_descricao FROM tb_marca ORDER BY mar_descricao")
            marcas = cursor.fetchall()
        else: flash("Não foi possível conectar ao banco de dados.", "danger")
    except pyodbc.Error as e: flash(f"Erro ao carregar dados do produto: {e}", "danger")
    finally:
        if conn: conn.close()
    
    return render_template('produto_form.html', produto=produto, itens_grade=json.dumps(itens_grade_dict, default=str), marcas=marcas)

@app.route('/produtos/excluir/<int:sec_codigo>/<int:esp_codigo>/<int:prd_codigo>', methods=['POST'])
def excluir_produto(sec_codigo, esp_codigo, prd_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")
        
        cursor = conn.cursor()
        sql = "{CALL pr_produto_d (?, ?, ?)}"
        cursor.execute(sql, (sec_codigo, esp_codigo, prd_codigo))
        conn.commit()
        flash('Produto excluído com sucesso!', 'success')
    except pyodbc.Error as e:
        conn.rollback()
        flash(f'Erro ao excluir produto: {e}', 'danger')
    finally:
        if conn: conn.close()
    return redirect('/produtos')

def handle_produto_post(is_edit=False):
    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")

        cursor = conn.cursor()
        usu_codigo = session.get('usuario_codigo')
        
        cursor.execute("DELETE FROM aux_item_produto WHERE usu_codigo = ?", usu_codigo)
        cursor.execute("DELETE FROM aux_atributo_item_produto WHERE usu_codigo = ?", usu_codigo)
        cursor.execute("DELETE FROM aux_atributo_produto WHERE usu_codigo = ?", usu_codigo)

        itens_grade = json.loads(request.form.get('itens_grade_json', '[]'))
        if not itens_grade: raise ValueError("O produto deve ter pelo menos um item na grade.")

        p_sec_codigo = int(r_form('sec_codigo'))
        p_esp_codigo = int(r_form('esp_codigo'))
        p_prd_codigo = int(r_form('prd_codigo')) if is_edit else 0

        for item in itens_grade:
            cursor.execute("INSERT INTO aux_item_produto(usu_codigo, sec_codigo, esp_codigo, prd_codigo, ipr_codigo, ipr_codigo_barra, ipr_preco_promocional) VALUES (?, ?, ?, ?, ?, ?, ?)",
                usu_codigo, p_sec_codigo, p_esp_codigo, p_prd_codigo,
                item.get('ipr_codigo'), item.get('ipr_codigo_barra'), item.get('ipr_preco_promocional'))

        if is_edit:
            params = (
                usu_codigo, p_sec_codigo, p_esp_codigo, p_prd_codigo,
                r_form('prd_descricao'), r_form('prd_descricao_reduzida'), int(r_form('mar_codigo', 0)) or None,
                r_form('prd_data_cadastro'), r_form('prd_unidade'), r_form('prd_data_ultima_compra'),
                r_form('prd_data_ultima_entrega'), float(r_form('prd_custo_medio', 0.0)), float(r_form('prd_ultimo_custo', 0.0)),
                float(r_form('prd_preco_medio', 0.0)), float(r_form('prd_aliquota_icms', 0.0)), r_form('prd_codigo_original'),
                1 if 'prd_ativo' in request.form else 0, r_form('prd_arquivo_foto'), r_form('prd_referencia_fornec'),
                r_form('clf_codigo'), int(r_form('usu_codigo_comprador', 0)) or None, int(r_form('und_codigo', 0)) or None,
                float(r_form('prd_valor_venda', 0.0)) or None, r_form('prd_origem'), float(r_form('prd_percentual_icms', 0.0)) or None,
                float(r_form('prd_percentual_ipi', 0.0)) or None, r_form('etq_codigo_padrao'), 1 if 'prd_permite_comprar' in request.form else 0,
                int(r_form('udc_codigo', 0)) or None, float(r_form('prd_valor_unidade_conversao', 0.0)) or None
            )
            sql = "{CALL pr_produto_u (" + "?,"*29 + "?)}"
        else:
            params = (
                usu_codigo, p_sec_codigo, p_esp_codigo, 0,
                r_form('prd_descricao'), r_form('prd_descricao_reduzida'), int(r_form('mar_codigo', 0)) or None,
                datetime.now(), r_form('prd_unidade'), None, None,
                float(r_form('prd_custo_medio', 0.0)), float(r_form('prd_ultimo_custo', 0.0)),
                float(r_form('prd_preco_medio', 0.0)), float(r_form('prd_aliquota_icms', 0.0)), r_form('prd_codigo_original'),
                1 if 'prd_ativo' in request.form else 0, r_form('prd_arquivo_foto'), r_form('prd_referencia_fornec'),
                r_form('clf_codigo'), int(r_form('usu_codigo_comprador', 0)) or None, int(r_form('und_codigo', 0)) or None,
                float(r_form('prd_valor_venda', 0.0)) or None, r_form('prd_origem'), float(r_form('prd_percentual_icms', 0.0)) or None,
                float(r_form('prd_percentual_ipi', 0.0)) or None, r_form('etq_codigo_padrao'), 1 if 'prd_permite_comprar' in request.form else 0,
                int(r_form('udc_codigo', 0)) or None, float(r_form('prd_valor_unidade_conversao', 0.0)) or None
            )
            sql = "{CALL pr_produto_i (" + "?,"*29 + "?)}"

        cursor.execute(sql, params)
        conn.commit()
        
        action = "atualizado" if is_edit else "cadastrado"
        flash(f'Produto {action} com sucesso!', 'success')
        return redirect('/produtos')

    except (pyodbc.Error, ValueError, Exception) as e:
        if conn: conn.rollback()
        flash(f'Erro ao salvar produto: {e}', 'danger')
    finally:
        if conn: conn.close()
    return redirect(request.url)

@app.route('/api/produtos/bulk-delete', methods=['POST'])
def bulk_delete_produtos():
    if 'usuario_logado' not in session:
        return jsonify({"error": "Não autorizado"}), 401

    data = request.get_json()
    product_keys = data.get('products')

    if not product_keys:
        return jsonify({"error": "Nenhum produto selecionado"}), 400

    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn:
            raise Exception("Não foi possível conectar ao banco de dados.")
        
        cursor = conn.cursor()
        sql = "{CALL pr_produto_d (?, ?, ?)}"
        
        # Itera sobre cada produto e executa a procedure de exclusão
        for keys in product_keys:
            cursor.execute(sql, (keys['sec_codigo'], keys['esp_codigo'], keys['prd_codigo']))
        
        conn.commit()
        return jsonify({"success": f"{len(product_keys)} produto(s) excluído(s) com sucesso!"}), 200

    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify({"error": f"Erro ao excluir produtos: {e}"}), 500
    finally:
        if conn: conn.close()

# --- Rotas de Pedidos ---
@app.route('/pedidos')
def visualizar_pedidos():
    if 'usuario_logado' not in session: return redirect('/')
    # Esta rota agora apenas renderiza a página. O conteúdo virá da API.
    colunas = ['Código', 'Fornecedor', 'Entrega Inicial', 'Entrega Final', 'Qtd Total', 'Valor Total', 'Status']
    return render_template('visualizar_pedidos.html', colunas=colunas)

@app.route('/pedidos/excluir/<int:ped_codigo>', methods=['POST'])
def excluir_pedido(ped_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")
        
        cursor = conn.cursor()
        sql = "{CALL pr_pedido_d (?)}"
        cursor.execute(sql, (ped_codigo))
        conn.commit()
        flash('Pedido excluído com sucesso!', 'success')
    except pyodbc.Error as e:
        conn.rollback()
        flash(f'Erro ao excluir pedido: {e}', 'danger')
    finally:
        if conn: conn.close()
    return redirect('/pedidos')

@app.route('/api/product_details/<string:full_code>')
def api_product_details(full_code):
    if 'usuario_logado' not in session: return jsonify({"error": "Não autorizado"}), 401
    
    conn = conectar_sqlserver()
    if not conn: return jsonify({"error": "DB connection failed"}), 500

    produto = None
    try:
        cursor = conn.cursor()
        query = """
            SELECT prd_codigo, sec_codigo, esp_codigo, prd_descricao, prd_ultimo_custo
            FROM tb_produto 
            WHERE CONCAT(FORMAT(sec_codigo, '000'), FORMAT(esp_codigo, '00'), FORMAT(prd_codigo, '0000')) = ?
        """
        cursor.execute(query, full_code)
        rows = rows_to_dicts(cursor)
        produto = rows[0] if rows else None
    except pyodbc.Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

    if produto:
        return jsonify(produto)
    else:
        return jsonify({"error": "Produto não encontrado"}), 404

@app.route('/api/pedidos')
def api_pedidos():
    """Nova rota de API para fornecer pedidos de forma paginada."""
    if 'usuario_logado' not in session: 
        return jsonify({"error": "Não autorizado"}), 401
    
    # Parâmetros para paginação e busca
    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('search', '', type=str)
    per_page = 100  # Quantidade de registros por página
    offset = (page - 1) * per_page

    conn = conectar_sqlserver()
    pedidos = []
    if conn:
        try:
            cursor = conn.cursor()
            base_query = """
                FROM tb_pedido p
                LEFT JOIN tb_pessoa_juridica pj ON p.pes_codigo = pj.pes_codigo
            """
            where_clause = ""
            params = []
            if search_term:
                like_term = f"%{search_term}%"
                # Permite buscar pelo nome do fornecedor ou pelo código do pedido
                where_clause = "WHERE pj.pju_razao_social LIKE ? OR CAST(p.ped_codigo AS VARCHAR(20)) LIKE ?"
                params.extend([like_term, like_term])

            # Query com paginação (OFFSET/FETCH)
            query = f"""
                SELECT 
                    p.ped_codigo AS 'Código',
                    pj.pju_razao_social AS 'Fornecedor',
                    p.ped_data_entrega_inicial AS 'Entrega Inicial',
                    p.ped_data_entrega_final AS 'Entrega Final',
                    p.ped_qtde_total AS 'Qtd Total',
                    p.ped_valor_total AS 'Valor Total',
                    p.ped_status AS 'Status'
                {base_query}
                {where_clause}
                ORDER BY p.ped_codigo DESC
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;
            """
            params.extend([offset, per_page])
            cursor.execute(query, params)
            pedidos = rows_to_dicts(cursor)
        except pyodbc.Error as e:
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
            
    return jsonify(pedidos)

@app.route('/pedidos/cadastrar', methods=['GET', 'POST'])
def cadastrar_pedido():
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_pedido_post()
    
    conn = conectar_sqlserver()
    fornecedores, compradores, produtos, filiais = get_pedido_form_data(conn)
    if conn: conn.close()
    return render_template('pedido_form.html', pedido=None, itens_pedido='[]', fornecedores=fornecedores, compradores=compradores, produtos=produtos, filiais=filiais)

@app.route('/pedidos/editar/<int:ped_codigo>', methods=['GET', 'POST'])
def editar_pedido(ped_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_pedido_post(is_edit=True)

    conn = None
    pedido = None
    itens_para_template = []
    fornecedores, compradores, produtos, filiais = [], [], [], []

    try:
        conn = conectar_sqlserver()
        if not conn:
            flash("Não foi possível conectar ao banco de dados.", "danger")
            return render_template('pedido_form.html', pedido=None, itens_pedido='[]',
                                   fornecedores=fornecedores, compradores=compradores, produtos=produtos, filiais=filiais)

        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tb_pedido WHERE ped_codigo=?", (ped_codigo,))
        pedido = cursor.fetchone()
        if not pedido:
            flash("Pedido não encontrado!", "danger")
            return redirect('/pedidos')
        
        # A query continua a mesma, pois já busca tudo que precisamos
        query_itens = """
            SELECT 
                ipd.*,
                prd.prd_descricao,
                prd.prd_ultimo_custo,
                CONCAT(FORMAT(prd.sec_codigo, '000'), FORMAT(prd.esp_codigo, '00'), FORMAT(prd.prd_codigo, '0000')) as full_code
            FROM tb_item_pedido ipd
            JOIN tb_produto prd ON ipd.sec_codigo = prd.sec_codigo 
                               AND ipd.esp_codigo = prd.esp_codigo 
                               AND ipd.prd_codigo = prd.prd_codigo
            WHERE ipd.ped_codigo = ?
        """
        cursor.execute(query_itens, (ped_codigo,))
        itens_do_banco = rows_to_dicts(cursor)

        produtos_agrupados = {}

        for item_row in itens_do_banco:
            full_code = item_row.get('full_code')
            
            if full_code not in produtos_agrupados:
                produtos_agrupados[full_code] = {
                    "full_code": full_code,
                    "descricao": item_row.get('prd_descricao'),
                    "sec": item_row.get('sec_codigo'),
                    "esp": item_row.get('esp_codigo'),
                    "prd": item_row.get('prd_codigo'),
                    "custo": float(item_row.get('ipd_valor_custo', 0)),
                    "packs": {}
                }

            pack_num = item_row.get('ipd_pack')
            produto_atual = produtos_agrupados[full_code]

            if pack_num not in produto_atual['packs']:
                
                # --- A GRANDE MUDANÇA ESTÁ AQUI ---
                # Em vez de ler um JSON, vamos usar o 'ipd_fator_grade' para
                # criar um objeto de 'grade simples' na hora.
                
                total_pecas_por_pack = item_row.get('ipd_fator_grade', 0)

                grade_details = {
                    "simpleGrade": {
                        "quantity": int(total_pecas_por_pack),
                        "multiplier": 1  # Assumimos multiplicador 1, pois não temos como saber
                    }
                }
                # --- FIM DA MUDANÇA ---

                produto_atual['packs'][pack_num] = {
                    "pack_num": pack_num,
                    "gradeDetails": grade_details, # Passamos a grade simples reconstruída
                    "filialDistribution": {}
                }
            
            pack_atual = produto_atual['packs'][pack_num]
            filial_code = item_row.get('fil_codigo')
            qtd_packs = item_row.get('ipd_fator_filial')
            pack_atual['filialDistribution'][filial_code] = qtd_packs
        
        for produto in produtos_agrupados.values():
            produto['packs'] = list(produto['packs'].values())
            itens_para_template.append(produto)
        
        fornecedores, compradores, produtos, filiais = get_pedido_form_data(conn)
        
    except pyodbc.Error as e:
        flash(f"Erro de banco de dados ao carregar pedido: {e}", "danger")
        app.logger.error(f"Erro de banco de dados ao editar pedido {ped_codigo}: {e}")
    except Exception as ex:
        flash(f"Ocorreu um erro inesperado: {ex}", "danger")
        app.logger.error(f"Erro inesperado ao editar pedido {ped_codigo}: {ex}")
    finally:
        if conn: conn.close()
        
    return render_template(
        'pedido_form.html', 
        pedido=pedido, 
        itens_pedido=json.dumps(itens_para_template, default=str), 
        fornecedores=fornecedores, 
        compradores=compradores, 
        produtos=produtos, 
        filiais=filiais
    )

def handle_pedido_post(is_edit=False):
    conn = None
    params = ()
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")

        usu_codigo_logado = session.get('usuario_codigo')
        if not usu_codigo_logado:
            raise ValueError("Sessão do usuário expirou ou é inválida.")

        cursor = conn.cursor()
        
        # Limpa as tabelas auxiliares para o usuário logado
        cursor.execute("DELETE FROM aux_item_pedido WHERE usu_codigo = ?", usu_codigo_logado)
        cursor.execute("DELETE FROM aux_pedido_produto WHERE usu_codigo = ?", usu_codigo_logado)
        
        current_ped_codigo = 0
        if is_edit:
            current_ped_codigo = int(r_form('ped_codigo', 0))
        else:
            cursor.execute("SELECT ISNULL(MAX(ped_codigo), 0) + 1 FROM tb_pedido")
            current_ped_codigo = cursor.fetchone()[0]

        itens_distribuicao = json.loads(request.form.get('itens_pedido_json', '[]'))
        if not itens_distribuicao: raise ValueError("O pedido deve ter pelo menos um item.")

        for item in itens_distribuicao:
            # O frontend envia 'pack_num', que estamos usando como o ipr_codigo/ipd_pack
            # O 'ipr_codigo' real (do item específico) não é usado na lógica de packs.
            pack_number = item.get('pack_num') 
            
            cursor.execute(
                """
                INSERT INTO aux_item_pedido 
                    (usu_codigo, ped_codigo, sec_codigo, esp_codigo, prd_codigo, 
                    ipr_codigo, fil_codigo, ipd_qtde_pedido, ipd_valor_custo, 
                    ipd_pack, ipd_fator_grade, ipd_fator_filial, ipd_grade_details_json) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, 
                (
                    usu_codigo_logado, 
                    current_ped_codigo, 
                    item.get('sec_codigo'), 
                    item.get('esp_codigo'), 
                    item.get('prd_codigo'),
                    pack_number, # ipr_codigo (usado como identificador do item do pedido)
                    item.get('fil_codigo'),
                    item.get('quantidade'),
                    item.get('custo'),
                    pack_number, # ipd_pack (o número do pack)
                    item.get('fator_grade'),
                    item.get('fator_filial'),
                    item.get('grade_details_json') # <- A INFORMAÇÃO CRÍTICA QUE FALTAVA
                )
            )

        # A lógica para 'aux_pedido_produto' precisa ser reconstruída a partir da lista original
        produtos_agrupados = {}
        for item in itens_distribuicao:
            prd_codigo = item['prd_codigo']
            if prd_codigo not in produtos_agrupados:
                produtos_agrupados[prd_codigo] = {
                    'sec_codigo': item['sec_codigo'], 
                    'esp_codigo': item['esp_codigo'], 
                    'prd_codigo': prd_codigo, 
                    'ppr_qtde_pedido': 0, 
                    'ppr_custo_medio': item['custo'] # Pega o custo do primeiro item encontrado
                }
            # Acumula a quantidade total de peças para o produto
            produtos_agrupados[prd_codigo]['ppr_qtde_pedido'] += item.get('quantidade', 0)

        for prd in produtos_agrupados.values():
            cursor.execute("INSERT INTO aux_pedido_produto (usu_codigo, ped_codigo, sec_codigo, esp_codigo, prd_codigo, ppr_qtde_pedido, ppr_custo_medio) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                           usu_codigo_logado, current_ped_codigo, prd['sec_codigo'], prd['esp_codigo'], prd['prd_codigo'], prd['ppr_qtde_pedido'], prd['ppr_custo_medio'])


        # O resto da função continua o mesmo...
        data_entrega_inicial_str = r_form('ped_data_entrega_inicial')
        data_entrega_final_str = r_form('ped_data_entrega_final')
        data_entrega_inicial = datetime.strptime(data_entrega_inicial_str, '%Y-%m-%d') if data_entrega_inicial_str else None
        data_entrega_final = datetime.strptime(data_entrega_final_str, '%Y-%m-%d') if data_entrega_final_str else None

        if is_edit:
            data_emissao_str = r_form('ped_data_emissao')
            data_emissao = datetime.strptime(data_emissao_str, '%Y-%m-%d') if data_emissao_str else None
            params = (usu_codigo_logado, current_ped_codigo, r_form('pes_codigo'), int(r_form('usu_codigo_comprador')), data_emissao, data_entrega_inicial, data_entrega_final, r_form('ped_status'), r_form('ped_observacao'), float(r_form('ped_qtde_total', 0)), float(r_form('ped_valor_total', 0)), float(r_form('ped_qtde_entregue_total', 0)), float(r_form('ped_custo_medio', 0)), r_form('ped_codigo_original'), r_form('ped_qualidade'), int(r_form('cpg_codigo', 0)) or None)
            sql = "{CALL pr_pedido_u (" + "?,"*15 + "?)}"
        else:
            params = (usu_codigo_logado, current_ped_codigo, r_form('pes_codigo'), int(r_form('usu_codigo_comprador')), datetime.now(), data_entrega_inicial, data_entrega_final, 'D', r_form('ped_observacao'), float(r_form('ped_qtde_total', 0)), float(r_form('ped_valor_total', 0)), 0, float(r_form('ped_custo_medio', 0)), r_form('ped_codigo_original'), r_form('ped_qualidade'), int(r_form('cpg_codigo', 0)) or None)
            sql = "{CALL pr_pedido_i (" + "?,"*15 + "?)}"
        
        cursor.execute(sql, params)
        conn.commit()
        
        action = "atualizado" if is_edit else "cadastrado"
        flash(f'Pedido {action} com sucesso!', 'success')
        return redirect('/pedidos')

    except (pyodbc.Error, ValueError, KeyError, Exception) as e:
        if conn: conn.rollback()
        error_message = str(e)
        app.logger.error(f"Erro ao salvar pedido: {error_message} | DADOS: {params}")
        flash(f'Erro ao salvar pedido: {error_message}<br><b>DADOS ENVIADOS:</b><pre>{params}</pre>', 'danger')
    finally:
        if conn: conn.close()
    
    return redirect(request.url)

@app.route('/api/produto/grade/<int:sec_codigo>/<int:esp_codigo>/<int:prd_codigo>')
def api_produto_grade(sec_codigo, esp_codigo, prd_codigo):
    """
    Retorna os atributos de Cor e Tamanho disponíveis para um produto específico.
    """
    if 'usuario_logado' not in session:
        return jsonify({"error": "Não autorizado"}), 401
    
    conn = conectar_sqlserver()
    grade = {'cores': [], 'tamanhos': []}
    if conn:
        try:
            cursor = conn.cursor()
            # Query para buscar cores
            query_cores = """
                SELECT DISTINCT aip.aip_descricao
                FROM tb_atributo_item_produto aip
                JOIN tb_tipo_atributo tpa ON aip.tpa_codigo = tpa.tpa_codigo
                WHERE aip.sec_codigo = ? AND aip.esp_codigo = ? AND aip.prd_codigo = ?
                  AND UPPER(tpa.tpa_descricao) LIKE '%COR%'
                ORDER BY aip.aip_descricao;
            """
            cursor.execute(query_cores, (sec_codigo, esp_codigo, prd_codigo))
            grade['cores'] = [row.aip_descricao for row in cursor.fetchall()]

            # Query para buscar tamanhos
            query_tamanhos = """
                SELECT DISTINCT aip.aip_descricao
                FROM tb_atributo_item_produto aip
                JOIN tb_tipo_atributo tpa ON aip.tpa_codigo = tpa.tpa_codigo
                WHERE aip.sec_codigo = ? AND aip.esp_codigo = ? AND aip.prd_codigo = ?
                  AND UPPER(tpa.tpa_descricao) LIKE '%TAMANHO%'
                ORDER BY aip.aip_descricao;
            """
            cursor.execute(query_tamanhos, (sec_codigo, esp_codigo, prd_codigo))
            grade['tamanhos'] = [row.aip_descricao for row in cursor.fetchall()]
            
        except pyodbc.Error as e:
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
    
    return jsonify(grade)

@app.route('/api/pedidos/bulk-delete', methods=['POST'])
def bulk_delete_pedidos():
    if 'usuario_logado' not in session:
        return jsonify({"error": "Não autorizado"}), 401

    data = request.get_json()
    order_ids = data.get('order_ids')

    if not order_ids:
        return jsonify({"error": "Nenhum pedido selecionado"}), 400

    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn:
            raise Exception("Não foi possível conectar ao banco de dados.")
        
        cursor = conn.cursor()
        sql = "{CALL pr_pedido_d (?)}"
        
        # Prepara os argumentos para a execução em lote
        params = [(order_id,) for order_id in order_ids]
        cursor.executemany(sql, params)
        
        conn.commit()
        return jsonify({"success": f"{len(order_ids)} pedido(s) excluído(s) com sucesso!"}), 200

    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify({"error": f"Erro ao excluir pedidos: {e}"}), 500
    finally:
        if conn: conn.close()

def get_pedido_form_data(conn):
    """Busca no banco de dados todas as informações necessárias para os formulários de pedido."""
    fornecedores, compradores, produtos, filiais = [], [], [], []
    if conn:
        try:
            cursor = conn.cursor()

            # Buscar Fornecedores
            cursor.execute("SELECT pes_codigo, pju_razao_social FROM tb_pessoa_juridica WHERE pju_razao_social IS NOT NULL ORDER BY pju_razao_social ")
            fornecedores = rows_to_dicts(cursor)

            # Buscar Compradores (usuários ativos)
            cursor.execute("SELECT usu_codigo, usu_login FROM tb_usuario WHERE usu_ativo = 1 ORDER BY usu_login")
            compradores = rows_to_dicts(cursor)

            # Buscar Produtos ativos que podem ser comprados
            # A query cria o 'full_code' que o template pedido_form.html espera
            query_produtos = """
                SELECT
                    prd.prd_codigo, prd.sec_codigo, prd.esp_codigo, prd.prd_descricao, prd.prd_ultimo_custo,
                    CONCAT(FORMAT(prd.sec_codigo, '000'), FORMAT(prd.esp_codigo, '00'), FORMAT(prd.prd_codigo, '0000')) as full_code
                FROM tb_produto prd
                WHERE prd.prd_ativo = 1 AND prd.prd_permite_comprar = 1
                ORDER BY prd.prd_descricao
            """
            cursor.execute(query_produtos)
            produtos = rows_to_dicts(cursor)

            # Buscar Filiais
            cursor.execute("SELECT fil_codigo, fil_descricao FROM tb_filial ORDER BY fil_codigo")
            filiais = rows_to_dicts(cursor)

        except pyodbc.Error as e:
            flash(f"Erro ao carregar dados para o formulário: {e}", "danger")

    return fornecedores, compradores, produtos, filiais

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
