package body Apps.Product.Views.Product_Views is

   function Product_Matrix_SQL return String is
   begin
      return
        "SELECT p.product_name, p.entrypoint_kind, pa.app_name, pa.enabled "
        & "FROM product_product p "
        & "LEFT JOIN product_product_app pa ON pa.product_name = p.product_name "
        & "ORDER BY p.product_name, pa.app_name";
   end Product_Matrix_SQL;

end Apps.Product.Views.Product_Views;
