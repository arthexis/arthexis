with Arthexis.ORM;

package body Apps.Product.Models.Product_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS product_product ("
         & "id INTEGER PRIMARY KEY, "
         & "product_name TEXT NOT NULL UNIQUE, "
         & "entrypoint_kind TEXT NOT NULL"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS product_product_app ("
         & "id INTEGER PRIMARY KEY, "
         & "product_name TEXT NOT NULL, "
         & "app_name TEXT NOT NULL, "
         & "enabled INTEGER NOT NULL DEFAULT 1, "
         & "UNIQUE(product_name, app_name), "
         & "FOREIGN KEY (product_name) REFERENCES product_product(product_name), "
         & "FOREIGN KEY (app_name) REFERENCES app_app(app_name)"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS product_product_model ("
         & "id INTEGER PRIMARY KEY, "
         & "product_name TEXT NOT NULL, "
         & "app_name TEXT NOT NULL, "
         & "model_name TEXT NOT NULL, "
         & "UNIQUE(product_name, app_name, model_name), "
         & "FOREIGN KEY (product_name) REFERENCES product_product(product_name), "
         & "FOREIGN KEY (app_name, model_name) REFERENCES model_model(app_name, model_name)"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO product_product(product_name, entrypoint_kind) "
         & "VALUES ('ocpp_charger_web', 'web');");
      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO product_product(product_name, entrypoint_kind) "
         & "VALUES ('ocpp_cli_simulator', 'cli');");
   end Install;

end Apps.Product.Models.Product_Models;
